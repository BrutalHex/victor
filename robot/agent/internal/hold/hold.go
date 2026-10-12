// Package hold pauses the wheels while someone talks (hub "listen while
// moving"). The hub sends "hold:<ms>" when its motion-noise detector hears a
// likely speech onset (or for a periodic listen window) and "release" when
// the turn is over. A hold always expires on its own (Max), so a lost hub can
// never leave a plan frozen forever; the veto and interlock run unchanged.
package hold

import (
	"strconv"
	"strings"
	"time"
)

const (
	Max      = 10 * time.Second       // longest single hold; the hub refreshes it
	BrakeFor = 150 * time.Millisecond // soft brake: wheel power ramps to 0
	RampFor  = 200 * time.Millisecond // audio after a ramp change is flagged (hub ignores onsets)
)

// Parse reads an action name: "hold:700" -> 700 ms, "release" -> 0. ok=false
// for anything else.
func Parse(name string) (d time.Duration, ok bool) {
	if name == "release" {
		return 0, true
	}
	if !strings.HasPrefix(name, "hold:") {
		return 0, false
	}
	ms, err := strconv.Atoi(strings.TrimPrefix(name, "hold:"))
	if err != nil || ms <= 0 {
		return 0, false
	}
	d = time.Duration(ms) * time.Millisecond
	if d > Max {
		d = Max
	}
	return d, true
}

// Ctl is one hold at a time; a second hold extends it.
type Ctl struct {
	until, since time.Time
	from         [2]int16
}

// Hold starts (or extends) a hold. lastPWM is what the wheels got last tick:
// the soft brake ramps down from it. Returns true when a new hold started.
func (c *Ctl) Hold(now time.Time, d time.Duration, lastPWM [4]int16) bool {
	if d > Max {
		d = Max
	}
	started := !c.Active(now)
	if started {
		c.since = now
		c.from = [2]int16{lastPWM[0], lastPWM[1]}
	}
	if u := now.Add(d); u.After(c.until) || started {
		c.until = u
	}
	return started
}

// Active: wheels are held now.
func (c *Ctl) Active(now time.Time) bool { return !c.since.IsZero() && now.Before(c.until) }

// Release ends a hold now; returns how long it lasted (0 = none was active).
func (c *Ctl) Release(now time.Time) time.Duration {
	if c.since.IsZero() {
		return 0
	}
	d := now.Sub(c.since)
	if now.After(c.until) {
		d = c.until.Sub(c.since)
	}
	c.since, c.until = time.Time{}, time.Time{}
	return d
}

// Expired reports (once) a hold that ran out on its own, with its length.
func (c *Ctl) Expired(now time.Time) (time.Duration, bool) {
	if c.since.IsZero() || now.Before(c.until) {
		return 0, false
	}
	return c.Release(now), true
}

// Brake is the wheel power during a hold: the last power ramping linearly to 0
// over BrakeFor (no jerk), then 0.
func (c *Ctl) Brake(now time.Time) (l, r int16) {
	el := now.Sub(c.since)
	if c.since.IsZero() || el >= BrakeFor {
		return 0, 0
	}
	k := 1 - float64(el)/float64(BrakeFor)
	return int16(float64(c.from[0]) * k), int16(float64(c.from[1]) * k)
}

// Ramp flags audio recorded just after a big motor change (start, stop,
// direction flip, a big power step, head/lift start or stop): the hub ignores
// speech onsets there (gear clunk is not a voice).
type Ramp struct {
	prev  [4]int16
	until time.Time
}

func sign(v int16, dead int16) int {
	switch {
	case v > dead:
		return 1
	case v < -dead:
		return -1
	}
	return 0
}

// Feed takes this tick's final PWM; true while within RampFor of a change.
func (r *Ramp) Feed(now time.Time, pwm [4]int16) bool {
	changed := false
	for i := 0; i < 2; i++ {
		d := int(pwm[i]) - int(r.prev[i])
		if sign(pwm[i], 0) != sign(r.prev[i], 0) || d > 8192 || d < -8192 {
			changed = true
		}
	}
	for i := 2; i < 4; i++ {
		if sign(pwm[i], 2000) != sign(r.prev[i], 2000) {
			changed = true
		}
	}
	r.prev = pwm
	if changed {
		r.until = now.Add(RampFor)
	}
	return now.Before(r.until)
}
