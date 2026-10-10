// Package drivectl is the shared wheel-speed controller for voice moves
// (internal/action) and wander (internal/wander).
//
// Live 10 Oct 2026: fixed power 0.18-0.38 (turns at a flat 0.24) did not
// overcome the treads' static friction on the floor: wheels twitched ~1 mm
// (8 encoder ticks) in 5 s turns. Breakaway needs more power than rolling, so:
//
//  1. breakaway: start at StartPower and ramp up (RampPerS) until the encoder
//     shows real motion (BreakMM), capped at KickMax;
//  2. rolling: drop back below the breakaway power and hold the target speed
//     with a proportional step every 100 ms, never below the learned floor and
//     never above RunMax; far too fast (2x target) halves the power at once;
//  3. stalled: KickMax for StallAfter without motion -> Stalled() (caller
//     turns away or gives up). Stopping again while rolling re-enters breakaway.
//
// 10 Oct 2026 (afternoon): 40 mm/s felt "really slow". Speeds are now
// configurable (Speeds / FromEnv, default ~120 mm/s, stock-Vector-like) and
// the rolling target ramps up at Accel mm/s^2 after breakaway so a faster
// cruise does not jerk or pop a wheelie. Lowering the target (obstacle ahead,
// end of a move) applies at once. StopMM / SpeedFor model the stopping
// distance so callers can slow down early; MaxMMps is the hard cap that
// keeps the cliff stop inside CliffMarginMM.
package drivectl

import (
	"math"
	"strconv"
	"time"
)

// Stopping model (conservative): the cliff/ToF reading is acted on within one
// 20 ms control tick plus motor latency (ReactS), then the treads stop at
// BrakeMMps2 (power cut + the reverse kick wander applies at a cliff).
const (
	ReactS        = 0.06
	BrakeMMps2    = 600.0
	CliffMarginMM = 25.0  // front cliff sensor lead over the treads' front contact (allowed overrun)
	MaxMMps       = 140.0 // StopMM(MaxMMps) <= CliffMarginMM; env values above are clamped
	HalfTrackMM   = 24.0
	MinApproach   = 30.0  // mm/s: slowest commanded speed while closing in
	SlowSpanMM    = 200.0 // start slowing this far before the stop distance
)

// StopMM: distance travelled from "danger seen" to standstill at v mm/s.
func StopMM(v float64) float64 {
	v = math.Abs(v)
	return v*ReactS + v*v/(2*BrakeMMps2)
}

// SpeedFor: the highest speed that still stops within distMM (inverse of StopMM).
func SpeedFor(distMM float64) float64 {
	if distMM <= 0 {
		return 0
	}
	a := BrakeMMps2
	return a * (-ReactS + math.Sqrt(ReactS*ReactS+2*distMM/a))
}

// Approach scales cruise down as an obstacle gets closer: full speed beyond
// stopAt+SlowSpanMM, MinApproach at stopAt, and never faster than what can
// still stop before stopAt. No valid reading = no limit.
func Approach(cruise float64, valid bool, proxMM, stopAt float64) float64 {
	if !valid || proxMM <= 0 {
		return cruise
	}
	gap := proxMM - stopAt
	frac := math.Max(0, math.Min(1, gap/SlowSpanMM))
	v := MinApproach + (cruise-MinApproach)*frac
	v = math.Min(v, math.Max(MinApproach, SpeedFor(gap)))
	return math.Min(cruise, v)
}

// Ending: slow down for the last mm of a move so it does not coast past.
func Ending(cruise, remainingMM float64) float64 {
	return math.Min(cruise, math.Max(MinApproach*0.8, SpeedFor(remainingMM)))
}

// Speeds are the configurable motion speeds (mm/s of wheel travel).
type Speeds struct {
	Explore float64 // wander cruise
	Drive   float64 // voice moves (forward, back up, come here)
	TurnDPS float64 // body turn rate, deg/s
	Accel   float64 // mm/s^2 target ramp after breakaway
}

func Defaults() Speeds { return Speeds{Explore: 120, Drive: 120, TurnDPS: 180, Accel: 400} }

// Cfg is what wander and action use (main sets it from the env at start).
var Cfg = Defaults()

// TurnMMps is the wheel speed for the configured turn rate.
func (s Speeds) TurnMMps() float64 { return s.TurnDPS * math.Pi / 180 * HalfTrackMM }

func clamp(v, lo, hi float64) float64 { return math.Max(lo, math.Min(hi, v)) }

// FromEnv reads VECTOR_EXPLORE_MMPS, VECTOR_DRIVE_MMPS, VECTOR_TURN_DPS and
// VECTOR_ACCEL_MMPS2; missing or bad values keep the default, and every value
// is clamped to the safe range (speeds 20..MaxMMps, turn 30..300 deg/s,
// accel 100..1000 mm/s^2).
func FromEnv(get func(string) string) Speeds {
	s := Defaults()
	num := func(k string, def float64) float64 {
		if v, err := strconv.ParseFloat(get(k), 64); err == nil && !math.IsNaN(v) && !math.IsInf(v, 0) {
			return v
		}
		return def
	}
	s.Explore = clamp(num("VECTOR_EXPLORE_MMPS", s.Explore), 20, MaxMMps)
	s.Drive = clamp(num("VECTOR_DRIVE_MMPS", s.Drive), 20, MaxMMps)
	s.TurnDPS = clamp(num("VECTOR_TURN_DPS", s.TurnDPS), 30, 300)
	if s.TurnMMps() > MaxMMps {
		s.TurnDPS = MaxMMps / HalfTrackMM * 180 / math.Pi
	}
	s.Accel = clamp(num("VECTOR_ACCEL_MMPS2", s.Accel), 100, 1000)
	return s
}

const (
	StartPower = 0.30
	RampPerS   = 0.6 // power per second while not moving
	KickMax    = 0.75
	RunMax     = 0.90 // was 0.60 at 40 mm/s; the speed loop, not this cap, sets the pace
	MinPower   = 0.12
	BreakMM    = 1.5 // encoder progress that counts as "moving"
	StallAfter = 1200 * time.Millisecond
	stopAfter  = 400 * time.Millisecond // no progress while rolling -> breakaway again
)

type Ctl struct {
	Target    float64 // mm/s of wheel travel
	Accel     float64 // mm/s^2 ramp of the rolling target (0 = jump)
	cur       float64 // ramped target
	rampAt    time.Time
	power     float64
	floor     float64 // learned: a bit below the breakaway power
	moving    bool
	lastAt    time.Time
	lastD     float64
	progAt    time.Time // last time progress >= BreakMM since then
	progD     float64
	atKickFor time.Duration
	started   bool
}

func New(targetMMps float64) *Ctl { return &Ctl{Target: targetMMps, Accel: Cfg.Accel} }

// SetTarget changes the speed; slowing down applies at once, speeding up
// follows the Accel ramp.
func (c *Ctl) SetTarget(v float64) {
	c.Target = v
	if c.cur > v {
		c.cur = v
	}
}

// Cur is the ramped target in force now.
func (c *Ctl) Cur() float64 { return c.cur }

// Reset starts a new move (keeps the learned floor).
func (c *Ctl) Reset(now time.Time) {
	c.power, c.moving, c.started = StartPower, false, true
	if c.floor > 0 {
		c.power = math.Max(StartPower, c.floor)
	}
	c.lastAt, c.lastD, c.progAt, c.progD, c.atKickFor = now, 0, now, 0, 0
	c.cur, c.rampAt = 0, now
}

func (c *Ctl) Moving() bool   { return c.moving }
func (c *Ctl) Power() float64 { return c.power }
func (c *Ctl) Floor() float64 { return c.floor }

// Stalled: full breakaway power for StallAfter and still no motion.
func (c *Ctl) Stalled() bool { return !c.moving && c.atKickFor >= StallAfter }

// Update takes the distance done so far in this move (mm, any sign; the
// magnitude is used) and returns the power (0..KickMax) to apply.
func (c *Ctl) Update(now time.Time, doneMM float64) float64 {
	if !c.started {
		c.Reset(now)
	}
	d := math.Abs(doneMM)
	dt := now.Sub(c.lastAt).Seconds()
	if d-c.progD >= BreakMM {
		if !c.moving {
			c.moving = true
			// rolling friction < static: learn a floor below the breakaway power
			c.floor = math.Max(MinPower, c.power*0.7)
			c.power = math.Max(c.floor, c.power*0.85)
			c.cur = math.Min(c.Target, MinApproach) // ramp from a gentle roll
			c.rampAt = now
		}
		c.progD, c.progAt = d, now
	}
	if c.moving && now.Sub(c.progAt) > stopAfter {
		c.moving = false // stuck again: breakaway once more
	}
	if dt <= 0 {
		return c.power
	}
	if !c.moving {
		c.power = math.Min(KickMax, c.power+RampPerS*dt)
		if c.power >= KickMax {
			c.atKickFor += now.Sub(c.lastAt)
		}
		c.lastAt = now
		c.lastD = d
		return c.power
	}
	c.atKickFor = 0
	if c.Accel <= 0 {
		c.cur = c.Target
	} else {
		c.cur = math.Min(c.Target, c.cur+c.Accel*now.Sub(c.rampAt).Seconds())
	}
	c.rampAt = now
	if dt >= 0.1 {
		v := (d - c.lastD) / dt
		tgt := c.cur
		err := tgt - v
		switch {
		case v > 2*tgt:
			c.power *= 0.5
		case math.Abs(err) > 0.15*tgt:
			// proportional, rate-limited: smooth accel, no wheelie
			step := clamp(err*0.0015, -0.08, 0.08)
			if math.Abs(step) < 0.015 {
				step = math.Copysign(0.015, step)
			}
			c.power += step
		}
		c.power = math.Max(math.Max(MinPower, c.floor*0.9), math.Min(RunMax, c.power))
		c.lastAt, c.lastD = now, d
	}
	return c.power
}
