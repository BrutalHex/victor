// Package action runs short, named robot actions for voice commands (fist
// bump, look at me, back up, get off the charger, ...). The hub only names the
// action; motor power is decided here, every tick, under the on-robot veto:
//
//   - any cliff / pickup / fall / battery / heartbeat veto aborts the action
//     and zeroes every motor at once;
//   - wheels never turn on the charger, except the "leave_charger" action;
//   - wheel moves are encoder-limited (a few cm) with a timeout, at
//     drivectl.Cfg.Drive (default 120 mm/s, VECTOR_DRIVE_MMPS) / TurnDPS,
//     ramped up after breakaway and slowed for the end of the move and,
//     driving forward, for a ToF obstacle ahead (main also aborts below
//     wander.ObstacleMM); /data/victor/voice-drive.disabled turns all wheel
//     moves off;
//   - the lift never moves on the charger (it is part of the CHARGE-LATCH
//     phrase there, which stays purely human).
package action

import (
	"math"
	"os"
	"strings"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/drivectl"
	"github.com/BrutalHex/victor/robot/agent/internal/vct1"
)

// Motor power is int16 = power * 0x7FFF (vector robot/hal hal_motors.cpp).
// Index order on the wire: 0 left wheel, 1 right wheel (direction -1), 2 lift, 3 head.
const (
	full          = 32767.0
	headPower     = 0.35 // stock head calibration drives at 0.3
	liftUpPower   = 0.55
	liftDownPower = -0.4                                 // stock lift calibration power
	mmPerTick     = 0.96 * 29.0 * 0.25 * math.Pi / 172.3 // wheel encoder count -> mm (HAL scale)
	halfTrackMM   = drivectl.HalfTrackMM
	obstacleMM    = 100 // mirrors wander.ObstacleMM (main aborts below it)
	planTimeout   = 15 * time.Second
	liftHold      = int16(4915) // 0.15 power keeps the lift up for a bump
)

// DisableFlag turns every wheel move off (var for tests).
var DisableFlag = "/data/victor/voice-drive.disabled"

type stepKind int

const (
	stHead stepKind = iota
	stLift
	stDrive
	stTurn
	stWait
	stBumpWait
)

type step struct {
	kind  stepKind
	power float64       // head/lift
	mm    float64       // drive distance (signed)
	deg   float64       // turn (+ left)
	dur   time.Duration // head/lift/wait duration, drive/turn timeout
	clip  string        // face clip to show from this step on ("" = keep)
}

// Plan is a named sequence.
type Plan struct {
	Name           string
	Steps          []step
	AllowOnCharger bool          // wheels may turn on the charger (leave_charger only)
	Timeout        time.Duration // 0 = planTimeout
}

// Plans the hub may name. Anything else is ignored.
var Plans = map[string]Plan{
	"look_up":   {Name: "look_up", Steps: []step{{kind: stHead, power: headPower, dur: 600 * time.Millisecond}}},
	"look_down": {Name: "look_down", Steps: []step{{kind: stHead, power: -headPower, dur: 600 * time.Millisecond}}},
	"look_at_me": {Name: "look_at_me", Steps: []step{
		{kind: stHead, power: headPower, dur: 700 * time.Millisecond, clip: "lookatme"},
		{kind: stWait, dur: 1500 * time.Millisecond}}},
	"nod": {Name: "nod", Steps: []step{
		{kind: stHead, power: headPower, dur: 300 * time.Millisecond},
		{kind: stHead, power: -headPower, dur: 300 * time.Millisecond},
		{kind: stHead, power: headPower, dur: 300 * time.Millisecond}}},
	"fistbump": {Name: "fistbump", Steps: []step{
		{kind: stHead, power: headPower, dur: 500 * time.Millisecond, clip: "fistbump_request"},
		{kind: stLift, power: liftUpPower, dur: 600 * time.Millisecond},
		{kind: stBumpWait, dur: 5 * time.Second},
		{kind: stLift, power: liftDownPower, dur: 700 * time.Millisecond}}},
	"dance": {Name: "dance", Steps: []step{
		{kind: stHead, power: headPower, dur: 250 * time.Millisecond, clip: "dance"},
		{kind: stTurn, deg: 15, dur: 1500 * time.Millisecond},
		{kind: stHead, power: -headPower, dur: 250 * time.Millisecond},
		{kind: stTurn, deg: -30, dur: 2 * time.Second},
		{kind: stHead, power: headPower, dur: 250 * time.Millisecond},
		{kind: stTurn, deg: 15, dur: 1500 * time.Millisecond},
		{kind: stHead, power: -headPower, dur: 250 * time.Millisecond}}},
	"forward": {Name: "forward", Steps: []step{{kind: stDrive, mm: 60, dur: 3 * time.Second}}},
	// supervised drive check (hub POST /action, never voice): 10 cm forward
	"forward_test":  {Name: "forward_test", Steps: []step{{kind: stDrive, mm: 100, dur: 5 * time.Second}}},
	"backup":        {Name: "backup", Steps: []step{{kind: stDrive, mm: -120, dur: 4 * time.Second}}},
	"turn_left":     {Name: "turn_left", Steps: []step{{kind: stTurn, deg: 90, dur: 4 * time.Second}}},
	"turn_right":    {Name: "turn_right", Steps: []step{{kind: stTurn, deg: -90, dur: 4 * time.Second}}},
	"turn_around":   {Name: "turn_around", Steps: []step{{kind: stTurn, deg: 180, dur: 6 * time.Second}}},
	"come_here":     {Name: "come_here", Steps: []step{{kind: stHead, power: headPower, dur: 400 * time.Millisecond, clip: "comehere"}, {kind: stDrive, mm: 100, dur: 4 * time.Second}}},
	"leave_charger": {Name: "leave_charger", AllowOnCharger: true, Steps: []step{{kind: stDrive, mm: 90, dur: 4 * time.Second, clip: "comeoff"}}},
	"stop":          {Name: "stop"},
}

// Tick input from the control loop.
type In struct {
	Now       time.Time
	OnCharger bool
	Abort     bool  // veto says stop everything (cliff, pickup, fall, battery, heartbeat)
	EncL      int32 // motors[0] position
	EncR      int32 // motors[1] position
	EncLift   int32 // motors[2] position
	ProxValid bool  // fresh ToF reading
	ProxMM    uint16
}

// Out for this tick.
type Out struct {
	Active bool
	PWM    [4]int16
	Clip   string // clip to start now ("" = none)
	Done   string // non-empty once when the plan ends: "ok", "aborted: why", "skipped: why", "bump"/"nobump"
}

type Runner struct {
	plan    Plan
	i       int
	started time.Time
	stepAt  time.Time
	encL0   int32
	encR0   int32
	lift0   int32
	ctl     *drivectl.Ctl
	notes   []string
	active  bool
	bumped  bool
	Log     func(string)
}

// Start a named plan; unknown names return false. "stop" just cancels.
func (r *Runner) Start(name string, now time.Time) bool {
	p, ok := Plans[name]
	if !ok {
		return false
	}
	r.plan, r.i, r.started, r.active, r.notes, r.bumped = p, 0, now, len(p.Steps) > 0, nil, false
	r.stepAt = time.Time{}
	return true
}

func (r *Runner) Active() bool { return r.active }
func (r *Runner) Name() string { return r.plan.Name }

func wheelsDisabled() bool {
	_, err := os.Stat(DisableFlag)
	return err == nil
}

func (r *Runner) note(s string) {
	r.notes = append(r.notes, s)
	if r.Log != nil {
		r.Log("action " + r.plan.Name + ": " + s)
	}
}

func (r *Runner) finish(why string) Out {
	r.active = false
	if len(r.notes) > 0 && why == "ok" {
		why = "ok (" + joinNotes(r.notes) + ")"
	}
	return Out{Done: why}
}

func joinNotes(n []string) string {
	s := ""
	for i, x := range n {
		if i > 0 {
			s += "; "
		}
		s += x
	}
	return s
}

// Tick advances the plan and returns the motor command.
func (r *Runner) Tick(in In) Out {
	if !r.active {
		return Out{}
	}
	if in.Abort {
		return r.finish("aborted: veto")
	}
	limit := r.plan.Timeout
	if limit <= 0 {
		limit = planTimeout
	}
	if in.Now.Sub(r.started) > limit {
		return r.finish("aborted: timeout")
	}
	if r.i >= len(r.plan.Steps) {
		return r.finish("ok")
	}
	st := r.plan.Steps[r.i]
	out := Out{Active: true}
	if r.stepAt.IsZero() {
		r.stepAt = in.Now
		r.encL0, r.encR0, r.lift0 = in.EncL, in.EncR, in.EncLift
		if r.ctl == nil {
			r.ctl = drivectl.New(drivectl.Cfg.Drive)
		}
		r.ctl.Reset(in.Now)
		if st.kind == stTurn {
			r.ctl.Target = drivectl.Cfg.TurnMMps()
		} else {
			r.ctl.Target = drivectl.Cfg.Drive
		}
		out.Clip = st.clip
		// skip steps that are not allowed right now
		switch st.kind {
		case stLift:
			if in.OnCharger {
				r.note("lift skipped on charger")
				return r.next(out)
			}
		case stBumpWait:
			if in.OnCharger {
				r.note("no lift on charger")
				return r.next(out)
			}
		case stDrive, stTurn:
			if wheelsDisabled() {
				r.note("wheels disabled by flag")
				return r.next(out)
			}
			if in.OnCharger && !r.plan.AllowOnCharger {
				r.note("wheels held: on charger")
				return r.next(out)
			}
		}
	}
	el := in.Now.Sub(r.stepAt)
	switch st.kind {
	case stHead:
		if el >= st.dur {
			return r.next(out)
		}
		out.PWM[3] = int16(st.power * full)
	case stLift:
		if el >= st.dur {
			return r.next(out)
		}
		out.PWM[2] = int16(st.power * full)
	case stWait:
		if el >= st.dur {
			return r.next(out)
		}
	case stBumpWait:
		// hold the lift up gently; a fist bump pushes it (encoder jump)
		out.PWM[2] = liftHold
		if abs32(in.EncLift-r.lift0) > 15 {
			r.bumped = true
			r.note("bump")
			out.Clip = "fistbump_success"
			return r.next(out)
		}
		if el >= st.dur {
			r.note("no bump")
			out.Clip = "fistbump_fail"
			return r.next(out)
		}
	case stDrive, stTurn:
		if in.OnCharger && !r.plan.AllowOnCharger {
			return r.finish("aborted: on charger")
		}
		dl := float64(in.EncL-r.encL0) * mmPerTick
		dr := -float64(in.EncR-r.encR0) * mmPerTick // right encoder counts backwards
		var done, target float64
		if st.kind == stDrive {
			done, target = (dl+dr)/2, st.mm
		} else {
			done, target = (dr-dl)/2, st.deg*math.Pi/180*halfTrackMM
		}
		if math.Abs(done) >= math.Abs(target) {
			return r.next(out)
		}
		if el >= st.dur {
			r.note("move timeout (stalled?)")
			return r.next(out)
		}
		// shared breakaway + speed controller (internal/drivectl): slow for the
		// end of the move and, driving forward, for an obstacle ahead
		cruise := drivectl.Cfg.Drive
		if st.kind == stTurn {
			cruise = drivectl.Cfg.TurnMMps()
		}
		v := drivectl.Ending(cruise, math.Abs(target)-math.Abs(done))
		if st.kind == stDrive && target > 0 {
			v = math.Min(v, drivectl.Approach(cruise, in.ProxValid, float64(in.ProxMM), obstacleMM))
		}
		r.ctl.SetTarget(v)
		pw := r.ctl.Update(in.Now, done)
		if r.ctl.Stalled() {
			r.note("move stalled (no wheel motion at full breakaway power)")
			return r.next(out)
		}
		sign := 1.0
		if target < 0 {
			sign = -1
		}
		p := sign * pw * full
		if st.kind == stDrive {
			out.PWM[0] = int16(p)  // left forward
			out.PWM[1] = int16(-p) // right wheel direction -1
		} else {
			out.PWM[0] = int16(-p) // turn left: left back, right forward
			out.PWM[1] = int16(-p)
		}
	}
	return out
}

func (r *Runner) next(out Out) Out {
	r.i++
	r.stepAt = time.Time{}
	if r.i >= len(r.plan.Steps) {
		o := r.finish("ok")
		o.Clip = out.Clip
		return o
	}
	out.Active = true
	out.PWM = [4]int16{}
	return out
}

// Reversing: the current step drives backwards (main then checks the rear
// cliff sensors and lets a front-edge veto pass, like the final interlock).
func (r *Runner) Reversing() bool {
	if !r.active || r.i >= len(r.plan.Steps) {
		return false
	}
	st := r.plan.Steps[r.i]
	return st.kind == stDrive && st.mm < 0
}

// Result maps a finished plan (Out.Done plus main's abort reason) to the
// SENSOR outcome code the hub turns into a spoken "why".
func Result(done, abortWhy string) uint8 {
	switch {
	case done == "":
		return vct1.ResultNone
	case strings.HasPrefix(abortWhy, "rear cliff"):
		return vct1.ResultRearCliff
	case strings.HasPrefix(abortWhy, "cliff"):
		return vct1.ResultCliff
	case strings.HasPrefix(abortWhy, "obstacle"):
		return vct1.ResultObstacle
	case abortWhy == "pickup" || abortWhy == "fall":
		return vct1.ResultPickup
	case abortWhy == "battery":
		return vct1.ResultBattery
	case abortWhy == "heartbeat":
		return vct1.ResultHubGone
	case strings.Contains(done, "timeout") && strings.HasPrefix(done, "aborted"):
		return vct1.ResultTimeout
	case strings.Contains(done, "on charger") || strings.Contains(done, "wheels held"):
		return vct1.ResultCharger
	case strings.Contains(done, "wheels disabled"):
		return vct1.ResultDisabled
	case strings.Contains(done, "stalled") || strings.Contains(done, "move timeout"):
		return vct1.ResultStalled
	case strings.HasPrefix(done, "ok") || done == "bump" || done == "nobump":
		return vct1.ResultOK
	}
	return vct1.ResultOK
}

// Bumped reports whether the last fist bump was felt.
func (r *Runner) Bumped() bool { return r.bumped }

func abs32(v int32) int32 {
	if v < 0 {
		return -v
	}
	return v
}
