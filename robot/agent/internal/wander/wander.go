// Package wander is slow autonomous desk/floor exploring, run on the robot
// (the on-robot veto owns the motors; the hub only says start/stop).
//
// Safety rules, all enforced here and tested:
//   - Hard enable flag /data/victor/explore.enabled (default absent = never
//     drives) AND an explicit start ("explore" voice command / action).
//   - Never on the charger (refuse to start; stop at once if docked).
//   - Calibrated cliff sensors required; refuse otherwise.
//   - Cliff wins: front cliff -> stop; the only motion then allowed is a
//     short slow reverse while both rear cliff sensors see floor, then a turn.
//   - Pickup, fall, low battery, hub-gone -> stop and end the session.
//   - ToF obstacle closer than ObstacleMM ahead -> stop, turn away.
//   - Stall (wheels commanded, encoders not moving) -> treat as a bump.
//   - ~40 mm/s, short legs, pauses with look-around, session time cap.
//   - Head moves only during pauses; never the lift (SSH latch gesture).
package wander

import (
	"github.com/BrutalHex/victor/robot/agent/internal/drivectl"
	"math"
	"math/rand"
	"os"
	"time"
)

var EnableFlag = "/data/victor/explore.enabled"

const (
	full       = 32767.0
	SpeedMMps  = 40.0
	ObstacleMM = 100
	// obstacleHold: a close return must persist this long (>= 2 ToF samples at
	// ~17 Hz) before it counts; single noisy returns are ignored.
	obstacleHold = 120 * time.Millisecond
	backMM       = 40.0
	mmPerTick    = 0.96 * 29.0 * 0.25 * math.Pi / 172.3
	halfTrackMM  = 24.0
	SessionMax   = 10 * time.Minute
	headPWM      = int16(8192) // 0.25 power
	// CliffMin mirrors veto: an uncalibrated channel below this is a cliff.
	CliffMin = 40
)

type State string

const (
	Off     State = "off"
	Pause   State = "pause"
	Drive   State = "drive"
	Turn    State = "turn"
	Backoff State = "backoff"
	Halt    State = "halt" // cliff with no safe reverse: stopped until restarted
)

type In struct {
	Now        time.Time
	OnCharger  bool
	CliffCal   bool      // cliffcal.Ready()
	Cliffs     [4]uint16 // 0,1 front; 2,3 rear
	Thresh     [4]uint16
	Pickup     bool // veto Pickup (all cliffs void / picked up)
	Fall       bool
	LowBattery bool
	HubGone    bool // heartbeat lost > 1 s
	ProxValid  bool
	ProxMM     uint16
	EncL, EncR int32
	Busy       bool // voice turn / action / petting: hold still, don't end
}

type Out struct {
	PWM    [4]int16
	Active bool   // session running (idle life should stay off while driving)
	Moving bool   // wheels or head commanded this tick
	Event  string // once per change, for the log
	State  State
}

type W struct {
	rng      *rand.Rand
	state    State
	since    time.Time
	started  time.Time
	encL0    int32
	encR0    int32
	target   float64 // mm (drive/backoff) or deg (turn)
	ctl      *drivectl.Ctl
	obsSince time.Time // close return first seen (zero = none)
	turnDir  float64
	pauseFor time.Duration
	look     int
	Why      string // last stop / refusal reason
}

func New(seed int64) *W {
	return &W{rng: rand.New(rand.NewSource(seed)), state: Off, ctl: drivectl.New(SpeedMMps)}
}

func FlagOn() bool {
	_, err := os.Stat(EnableFlag)
	return err == nil
}

func (w *W) State() State { return w.state }
func (w *W) Active() bool { return w.state != Off }

// Start begins a session; returns "" or why not.
func (w *W) Start(in In, flag bool) string {
	switch {
	case !flag:
		w.Why = "explore flag off"
	case in.OnCharger:
		w.Why = "on charger"
	case !in.CliffCal:
		w.Why = "cliff sensors not calibrated"
	case in.Pickup || in.Fall:
		w.Why = "picked up"
	case in.LowBattery:
		w.Why = "low battery"
	case w.frontCliff(in) || w.rearCliff(in):
		w.Why = "at an edge"
	default:
		w.Why = ""
		w.started = in.Now
		w.enter(Pause, in)
		w.pauseFor = time.Second
		return ""
	}
	return w.Why
}

// Stop ends the session (voice "stop", action "stop", explore_stop).
func (w *W) Stop(why string) {
	if w.state != Off {
		w.Why = why
	}
	w.state = Off
}

func (w *W) enter(s State, in In) {
	w.state, w.since = s, in.Now
	w.encL0, w.encR0 = in.EncL, in.EncR
	w.ctl.Reset(in.Now)
	w.obsSince = time.Time{}
}

// obstacle: a valid close return that has lasted obstacleHold.
func (w *W) obstacle(in In) bool {
	if !(in.ProxValid && in.ProxMM > 0 && in.ProxMM < ObstacleMM) {
		w.obsSince = time.Time{}
		return false
	}
	if w.obsSince.IsZero() {
		w.obsSince = in.Now
	}
	return in.Now.Sub(w.obsSince) >= obstacleHold
}

func chan_(v, th uint16) bool { // true = no floor under this sensor
	return v < CliffMin || (th > 0 && v < th) // same rule as veto.frontCliff
}

func (w *W) frontCliff(in In) bool {
	return chan_(in.Cliffs[0], in.Thresh[0]) || chan_(in.Cliffs[1], in.Thresh[1])
}

func (w *W) rearCliff(in In) bool {
	return chan_(in.Cliffs[2], in.Thresh[2]) || chan_(in.Cliffs[3], in.Thresh[3])
}

func (w *W) progress(in In) (fwd, turnDeg float64) {
	dl := float64(in.EncL-w.encL0) * mmPerTick
	dr := -float64(in.EncR-w.encR0) * mmPerTick // right encoder counts backwards
	return (dl + dr) / 2, (dr - dl) / 2 / halfTrackMM * 180 / math.Pi
}

func wheels(left, right float64) [4]int16 {
	// index 0 left wheel, 1 right wheel (HAL direction -1)
	return [4]int16{int16(left * full), int16(-right * full), 0, 0}
}

func (w *W) startTurn(in In, why string) string {
	deg := 90 + float64(w.rng.Intn(61))
	if w.rng.Intn(2) == 0 {
		deg = -deg
	}
	w.enter(Turn, in)
	w.target = deg
	return "turn " + why
}

func (w *W) Tick(in In) Out {
	out := Out{State: w.state}
	if w.state == Off {
		return out
	}
	out.Active = true
	end := func(why string) Out {
		w.Stop(why)
		return Out{State: Off, Event: "stop: " + why}
	}
	switch {
	case in.OnCharger:
		return end("on charger")
	case in.Pickup:
		return end("picked up")
	case in.Fall:
		return end("fall")
	case in.LowBattery:
		return end("low battery")
	case in.HubGone:
		return end("hub gone")
	case in.Now.Sub(w.started) > SessionMax:
		return end("session time cap")
	}
	if in.Busy {
		// a voice turn or command owns the robot: hold still, resume later
		if w.state == Drive || w.state == Turn {
			w.enter(Pause, in)
			w.pauseFor = 2 * time.Second
		}
		w.since = in.Now
		return out
	}
	// Cliff wins in every state except a reverse that is moving away from it.
	if w.frontCliff(in) && w.state != Backoff && w.state != Halt {
		if w.rearCliff(in) {
			w.enter(Halt, in)
			w.Why = "cliff front and rear"
			out.State, out.Event = Halt, "halt: cliff front and rear"
			return out
		}
		w.enter(Backoff, in)
		w.target = -backMM
		out.State, out.Event = Backoff, "cliff: backing off"
		return out // zero this tick
	}
	el := in.Now.Sub(w.since)
	switch w.state {
	case Halt:
		return out // stopped; needs a person (stop/start again)
	case Pause:
		if el >= w.pauseFor {
			leg := 150 + float64(w.rng.Intn(250))
			w.enter(Drive, in)
			w.target = leg
			out.State, out.Event = Drive, "drive"
			return out
		}
		// look around: two short head nudges per pause
		if w.pauseFor >= 2*time.Second {
			ph := el.Seconds()
			if (ph > 0.3 && ph < 0.6) || (ph > 1.4 && ph < 1.6) {
				out.PWM[3] = headPWM
				out.Moving = true
			} else if ph > 0.9 && ph < 1.2 {
				out.PWM[3] = -headPWM
				out.Moving = true
			}
		}
	case Drive:
		done, _ := w.progress(in)
		if w.obstacle(in) {
			out.Event = w.startTurn(in, "obstacle")
			out.State = w.state
			return out
		}
		if done >= w.target || el > time.Duration(w.target/SpeedMMps*2.5*float64(time.Second)) {
			w.enter(Pause, in)
			w.pauseFor = 2*time.Second + time.Duration(w.rng.Intn(2000))*time.Millisecond
			out.State, out.Event = Pause, "pause"
			return out
		}
		p := w.ctl.Update(in.Now, done)
		if w.ctl.Stalled() {
			out.Event = w.startTurn(in, "stall")
			out.State = w.state
			return out
		}
		out.PWM = wheels(p, p)
		out.Moving = true
	case Backoff:
		done, _ := w.progress(in)
		if w.rearCliff(in) {
			w.enter(Halt, in)
			w.Why = "cliff behind while backing off"
			out.State, out.Event = Halt, "halt: rear cliff"
			return out
		}
		if done <= w.target || el > 3*time.Second {
			out.Event = w.startTurn(in, "after cliff")
			out.State = w.state
			return out
		}
		p := w.ctl.Update(in.Now, done)
		out.PWM = wheels(-p, -p)
		out.Moving = true
	case Turn:
		_, deg := w.progress(in)
		if math.Abs(deg) >= math.Abs(w.target) || el > 5*time.Second || w.ctl.Stalled() {
			w.enter(Pause, in)
			w.pauseFor = 1500 * time.Millisecond
			out.State, out.Event = Pause, "pause"
			return out
		}
		s := 1.0
		if w.target < 0 {
			s = -1
		}
		// +deg = left: right wheel forward, left wheel back. Speed control on
		// wheel travel (arc mm), same breakaway controller as driving.
		p := w.ctl.Update(in.Now, deg*math.Pi/180*halfTrackMM)
		out.PWM = wheels(-s*p, s*p)
		out.Moving = true
	}
	out.State = w.state
	return out
}
