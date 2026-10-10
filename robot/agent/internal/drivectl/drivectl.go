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
package drivectl

import (
	"math"
	"time"
)

const (
	StartPower = 0.30
	RampPerS   = 0.6 // power per second while not moving
	KickMax    = 0.75
	RunMax     = 0.60
	MinPower   = 0.12
	BreakMM    = 1.5 // encoder progress that counts as "moving"
	StallAfter = 1200 * time.Millisecond
	stopAfter  = 400 * time.Millisecond // no progress while rolling -> breakaway again
)

type Ctl struct {
	Target    float64 // mm/s of wheel travel
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

func New(targetMMps float64) *Ctl { return &Ctl{Target: targetMMps} }

// Reset starts a new move (keeps the learned floor).
func (c *Ctl) Reset(now time.Time) {
	c.power, c.moving, c.started = StartPower, false, true
	if c.floor > 0 {
		c.power = math.Max(StartPower, c.floor)
	}
	c.lastAt, c.lastD, c.progAt, c.progD, c.atKickFor = now, 0, now, 0, 0
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
	if dt >= 0.1 {
		v := (d - c.lastD) / dt
		switch {
		case v > 2*c.Target:
			c.power *= 0.5
		case v > 1.3*c.Target:
			c.power -= 0.03
		case v < 0.7*c.Target:
			c.power += 0.03
		}
		c.power = math.Max(math.Max(MinPower, c.floor*0.9), math.Min(RunMax, c.power))
		c.lastAt, c.lastD = now, d
	}
	return c.power
}
