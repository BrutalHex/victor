package action

import (
	"fmt"
	"github.com/BrutalHex/victor/robot/agent/internal/drivectl"
	"math"
	"strconv"
	"strings"
	"time"
)

// Dynamic plans: the hub's model turns a sentence ("drive in a square",
// "go forward a bit, then turn left and look up") into an ordered list of
// the same primitive steps the named plans use. The hub validates and clamps
// first; everything is clamped again here, because motor power is decided on
// the robot. Wire format (CMD_ACTION payload, lower case):
//
//	plan:drive 200;turn 90;head up;lift down;wait 800;face expr_happy;trick dance
//
// Unknown or malformed steps are dropped (noted), never guessed.
const (
	PlanPrefix     = "plan:"
	MaxSteps       = 12               // top-level steps per plan
	MaxDriveMM     = 500.0            // per drive step (signed)
	MaxTurnDeg     = 720.0            // per turn step (signed)
	MaxWait        = 5 * time.Second  // per wait step
	MaxTravelMM    = 2000.0           // wheel travel per plan (drives + turn arcs)
	MaxPlanTime    = 30 * time.Second // per-plan timeout (named plans keep planTimeout)
	minDriveMM     = 5.0
	minTurnDeg     = 3.0
	driveSlackTime = 2 * time.Second
)

// ValidClip reports whether a face clip exists (main wires face.ClipLen).
var ValidClip = func(string) bool { return true }

// Tricks a plan may embed by name (the named plans minus the charger exit,
// the supervised test drive and stop).
var Tricks = []string{"dance", "nod", "fistbump", "look_at_me", "look_up", "look_down", "come_here", "forward", "backup", "turn_left", "turn_right", "turn_around"}

func isTrick(n string) bool {
	for _, t := range Tricks {
		if t == n {
			return true
		}
	}
	return false
}

func driveTimeout(mm float64) time.Duration {
	return driveSlackTime + time.Duration(math.Abs(mm)/60*float64(time.Second))
}

func turnTimeout(deg float64) time.Duration {
	return driveSlackTime + time.Duration(math.Abs(deg)/60*float64(time.Second))
}

// estimate is the expected run time of one step at the configured speeds
// plus ramp/settle slack (the 30 s cap; the runtime timeout still applies).
func estimate(s step) time.Duration {
	sec := func(v float64) time.Duration { return time.Duration(v * float64(time.Second)) }
	switch s.kind {
	case stDrive:
		return sec(math.Abs(s.mm)/math.Max(drivectl.Cfg.Drive, 30) + 0.8)
	case stTurn:
		return sec(math.Abs(s.deg)*math.Pi/180*halfTrackMM/math.Max(drivectl.Cfg.TurnMMps(), 20) + 0.8)
	default:
		return s.dur
	}
}

func clampF(v, lim float64) float64 {
	return math.Max(-lim, math.Min(lim, v))
}

// ParsePlan builds a dynamic plan from the wire text. It never fails: bad
// steps are dropped and reported in notes; caps truncate the tail.
func ParsePlan(text string) (Plan, []string) {
	var notes []string
	p := Plan{Name: "plan", Timeout: MaxPlanTime}
	body := strings.TrimPrefix(strings.TrimSpace(text), PlanPrefix)
	travel := 0.0
	total := time.Duration(0)
	n, faces := 0, 0
	for _, raw := range strings.FieldsFunc(body, func(r rune) bool { return r == ';' || r == '\n' }) {
		f := strings.Fields(strings.ToLower(strings.TrimSpace(raw)))
		if len(f) == 0 {
			continue
		}
		if f[0] == "face" && faces >= 2*MaxSteps {
			continue
		}
		if f[0] != "face" && n >= MaxSteps {
			notes = append(notes, fmt.Sprintf("cut at %d steps", MaxSteps))
			break
		}
		arg := ""
		if len(f) > 1 {
			arg = f[1]
		}
		num, numErr := strconv.ParseFloat(arg, 64)
		if numErr == nil && (math.IsNaN(num) || math.IsInf(num, 0)) {
			numErr = fmt.Errorf("not finite")
		}
		var add []step
		var dist float64
		switch f[0] {
		case "drive":
			if numErr != nil {
				notes = append(notes, "bad drive "+arg)
				continue
			}
			mm := clampF(num, MaxDriveMM)
			if math.Abs(mm) < minDriveMM {
				continue
			}
			add, dist = []step{{kind: stDrive, mm: mm, dur: driveTimeout(mm)}}, math.Abs(mm)
		case "turn":
			if numErr != nil {
				notes = append(notes, "bad turn "+arg)
				continue
			}
			deg := clampF(num, MaxTurnDeg)
			if math.Abs(deg) < minTurnDeg {
				continue
			}
			add, dist = []step{{kind: stTurn, deg: deg, dur: turnTimeout(deg)}}, math.Abs(deg)*math.Pi/180*halfTrackMM
		case "head":
			switch arg {
			case "up":
				add = []step{{kind: stHead, power: headPower, dur: 600 * time.Millisecond}}
			case "down":
				add = []step{{kind: stHead, power: -headPower, dur: 600 * time.Millisecond}}
			case "mid", "middle", "center", "straight":
				add = []step{{kind: stHead, power: -headPower, dur: 600 * time.Millisecond}, {kind: stHead, power: headPower, dur: 260 * time.Millisecond}}
			case "nod":
				add = Plans["nod"].Steps
			case "bob": // small happy bob, ends where it started
				add = []step{{kind: stHead, power: headPower, dur: 200 * time.Millisecond}, {kind: stHead, power: -headPower, dur: 200 * time.Millisecond}}
			case "droop": // sad: head sinks a little
				add = []step{{kind: stHead, power: -headPower * 0.7, dur: 450 * time.Millisecond}}
			default:
				notes = append(notes, "bad head "+arg)
				continue
			}
		case "lift":
			switch arg {
			case "up":
				add = []step{{kind: stLift, power: liftUpPower, dur: 600 * time.Millisecond}}
			case "down":
				add = []step{{kind: stLift, power: liftDownPower, dur: 700 * time.Millisecond}}
			default:
				notes = append(notes, "bad lift "+arg)
				continue
			}
		case "wait":
			if numErr != nil || num < 0 {
				notes = append(notes, "bad wait "+arg)
				continue
			}
			d := time.Duration(num) * time.Millisecond
			if d > MaxWait {
				d = MaxWait
			}
			add = []step{{kind: stWait, dur: d}}
		case "face":
			if arg == "" || !ValidClip(arg) {
				notes = append(notes, "unknown face "+arg)
				continue
			}
			add = []step{{kind: stWait, clip: arg}}
		case "trick":
			if !isTrick(arg) {
				notes = append(notes, "unknown trick "+arg)
				continue
			}
			add = Plans[arg].Steps
			for _, s := range add {
				if s.kind == stDrive {
					dist += math.Abs(s.mm)
				} else if s.kind == stTurn {
					dist += math.Abs(s.deg) * math.Pi / 180 * halfTrackMM
				}
			}
		default:
			notes = append(notes, "unknown step "+f[0])
			continue
		}
		var d time.Duration
		for _, s := range add {
			d += estimate(s)
		}
		if travel+dist > MaxTravelMM+0.5 {
			notes = append(notes, fmt.Sprintf("cut: travel over %.0f mm", MaxTravelMM))
			break
		}
		if total+d > MaxPlanTime {
			notes = append(notes, fmt.Sprintf("cut: longer than %s", MaxPlanTime))
			break
		}
		travel += dist
		total += d
		p.Steps = append(p.Steps, add...)
		if f[0] == "face" {
			faces++
		} else {
			n++
		}
	}
	return p, notes
}

// StartText starts a named plan or a "plan:" text. False = unknown/empty.
func (r *Runner) StartText(text string, now time.Time) bool {
	if !strings.HasPrefix(text, PlanPrefix) {
		return r.Start(text, now)
	}
	p, notes := ParsePlan(text)
	r.plan, r.i, r.started, r.active, r.notes, r.bumped = p, 0, now, len(p.Steps) > 0, nil, false
	r.stepAt = time.Time{}
	for _, n := range notes {
		r.note(n)
	}
	return len(p.Steps) > 0
}

// Cancel stops a running plan at once (voice "stop", back button). The
// caller zeroes the motors this tick (the runner returns no power once idle).
func (r *Runner) Cancel(why string) bool {
	if !r.active {
		return false
	}
	r.active = false
	r.note("cancelled: " + why)
	return true
}
