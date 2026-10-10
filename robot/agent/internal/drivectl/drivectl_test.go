package drivectl

import (
	"testing"
	"time"
)

// wheel model: static friction breaks at `brk` power, then speed ~ k*(p - roll)
type wheel struct {
	brk, roll, k, pos float64
	moving            bool
}

func (w *wheel) step(p, dt float64) {
	if !w.moving && p >= w.brk {
		w.moving = true
	}
	if w.moving {
		v := w.k * (p - w.roll)
		if v <= 0 {
			w.moving, v = false, 0
		}
		w.pos += v * dt
	}
}

func run(t *testing.T, w *wheel, secs float64) (*Ctl, []float64, []float64) {
	c := New(40)
	t0 := time.Unix(0, 0)
	c.Reset(t0)
	var ps, vs []float64
	last := 0.0
	for i := 1; i <= int(secs*200); i++ {
		now := t0.Add(time.Duration(i) * 5 * time.Millisecond)
		p := c.Update(now, w.pos)
		w.step(p, 0.005)
		if i%20 == 0 {
			vs = append(vs, (w.pos-last)/0.1)
			last = w.pos
		}
		ps = append(ps, p)
	}
	return c, ps, vs
}

// The live failure: breakaway at ~0.45 power, the old fixed 0.24/0.38 never moved.
func TestBreaksAwayThenHoldsSafeSpeed(t *testing.T) {
	w := &wheel{brk: 0.45, roll: 0.15, k: 160}
	c, ps, vs := run(t, w, 4)
	if !c.Moving() || w.pos < 80 {
		t.Fatalf("did not drive: pos %.1f moving %v", w.pos, c.Moving())
	}
	for _, p := range ps {
		if p > KickMax+1e-9 {
			t.Fatalf("power %f above kick cap", p)
		}
	}
	// after settling (last 2 s) speed stays near 40 mm/s, never runaway
	for _, v := range vs[20:] {
		if v > 70 || v < 15 {
			t.Fatalf("speed %.1f out of band, all %v", v, vs)
		}
	}
	if c.Floor() < MinPower || c.Floor() > 0.45 {
		t.Fatalf("floor %f", c.Floor())
	}
}

func TestEasyFloorDoesNotLurch(t *testing.T) {
	w := &wheel{brk: 0.2, roll: 0.05, k: 300}
	_, _, vs := run(t, w, 3)
	for i, v := range vs[5:] {
		if v > 90 {
			t.Fatalf("lurch %.1f mm/s at %d: %v", v, i, vs)
		}
	}
}

func TestStallReportedWhenItCannotMove(t *testing.T) {
	w := &wheel{brk: 5, roll: 0.1, k: 100} // blocked
	c, ps, _ := run(t, w, 3)
	if !c.Stalled() {
		t.Fatalf("expected stall, power %f", ps[len(ps)-1])
	}
	if w.pos != 0 {
		t.Fatal("moved")
	}
}

func TestStuckAgainRampsAgain(t *testing.T) {
	w := &wheel{brk: 0.45, roll: 0.15, k: 160}
	c := New(40)
	t0 := time.Unix(0, 0)
	c.Reset(t0)
	now := t0
	for i := 0; i < 400; i++ {
		now = now.Add(5 * time.Millisecond)
		w.step(c.Update(now, w.pos), 0.005)
	}
	if !c.Moving() {
		t.Fatal("not moving")
	}
	pos := w.pos
	for i := 0; i < 200; i++ { // wheel now blocked (hit a rug edge)
		now = now.Add(5 * time.Millisecond)
		c.Update(now, pos)
	}
	if c.Moving() || c.Power() < 0.5 {
		t.Fatalf("should be ramping again: moving %v power %f", c.Moving(), c.Power())
	}
}
