package wander

import (
	"math"
	"testing"
	"time"
)

// 12 Oct 14:35: the hub's VAD fired every ~3 s (busy ~2.5 s of each 3 s).
// Explore must keep making progress between holds instead of freezing.
func TestBusyFlickerDoesNotFreeze(t *testing.T) {
	s := newSim()
	w := New(21)
	w.Start(s.in, true)
	in := s.in
	moved := 0.0
	for i := 0; i < 60*50; i++ {
		in.Busy = (i % 150) < 125
		o := w.Tick(in)
		if in.Busy && o.PWM[0] != 0 {
			t.Fatal("drove while busy")
		}
		px, py := s.x, s.y
		in = s.step(o)
		moved += math.Hypot(s.x-px, s.y-py)
	}
	if moved < 60 || !w.Active() {
		t.Fatalf("froze: moved %.0f mm active=%v", moved, w.Active())
	}
}

// Wedged (no ToF, wheels powered, encoders still): blocked within 1.5 s,
// then back up / turn.
func TestPushWatchdog(t *testing.T) {
	s := newSim()
	w := New(22)
	w.Start(s.in, true)
	in := s.in
	var pushFrom time.Time
	for i := 0; i < 20*50; i++ {
		o := w.Tick(in)
		fwd := o.PWM[0] > 0 && o.PWM[1] < 0
		if o.State == Drive && fwd {
			s.stuck = true
			if pushFrom.IsZero() {
				pushFrom = in.Now
			}
			if in.Now.Sub(pushFrom) > 1500*time.Millisecond {
				t.Fatalf("pushed %v without reacting", in.Now.Sub(pushFrom))
			}
		} else {
			if !pushFrom.IsZero() {
				return // reacted
			}
			s.stuck = false
		}
		in = s.step(o)
	}
	t.Fatal("never drove")
}
