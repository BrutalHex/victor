// Package button turns the raw backpack button level (sampled each agent
// tick) into debounced presses.
//
// A press counts once the level has read pressed for Debounce after a
// stable release; the next press needs a release of Debounce first. A level
// stuck "pressed" (bad parse, held finger) therefore gives one press, never a
// stream. Presses is a running count sent to the hub in SENSOR; the hub turns
// increments into button_press events (a lost UDP packet loses nothing).
package button

import "time"

const Debounce = 40 * time.Millisecond

type Debouncer struct {
	Presses uint16
	pressed bool // debounced state
	raw     bool
	since   time.Time
	started bool
}

// Feed takes one raw sample; true when it completes a new press.
func (d *Debouncer) Feed(raw bool, now time.Time) bool {
	if !d.started {
		// first sample: adopt the level without a press (a held button at
		// agent start is not a press)
		d.started, d.raw, d.pressed, d.since = true, raw, raw, now
		return false
	}
	if raw != d.raw {
		d.raw, d.since = raw, now
	}
	if d.raw == d.pressed || now.Sub(d.since) < Debounce {
		return false
	}
	d.pressed = d.raw
	if d.pressed {
		d.Presses++
		return true
	}
	return false
}

// Pressed is the debounced level.
func (d *Debouncer) Pressed() bool { return d.pressed }
