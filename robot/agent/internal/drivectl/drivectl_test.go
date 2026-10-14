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

func TestStopModelAndCap(t *testing.T) {
	for _, d := range []float64{5, 20, 60, 200} {
		if got := StopMM(SpeedFor(d)); got < d-0.01 || got > d+0.01 {
			t.Fatalf("SpeedFor/StopMM not inverse at %.0f: %f", d, got)
		}
	}
	if StopMM(MaxMMps) > CliffMarginMM {
		t.Fatalf("MaxMMps %.0f stops in %.1f mm > margin %.0f", MaxMMps, StopMM(MaxMMps), CliffMarginMM)
	}
	d := Defaults()
	if d.Explore < 100 || d.Drive < 100 || d.Explore > MaxMMps || d.Drive > MaxMMps || d.TurnMMps() > MaxMMps {
		t.Fatalf("defaults %+v", d)
	}
}

func TestFromEnvClamps(t *testing.T) {
	m := map[string]string{"VECTOR_EXPLORE_MMPS": "500", "VECTOR_DRIVE_MMPS": "90", "VECTOR_TURN_DPS": "abc", "VECTOR_ACCEL_MMPS2": "5"}
	s := FromEnv(func(k string) string { return m[k] })
	if s.Explore != MaxMMps || s.Drive != 90 || s.TurnDPS != Defaults().TurnDPS || s.Accel != 100 {
		t.Fatalf("%+v", s)
	}
	if s := FromEnv(func(string) string { return "" }); s != Defaults() {
		t.Fatalf("empty env %+v", s)
	}
	m = map[string]string{"VECTOR_TURN_DPS": "1000", "VECTOR_EXPLORE_MMPS": "-3"}
	s = FromEnv(func(k string) string { return m[k] })
	if s.TurnMMps() > MaxMMps+1e-9 || s.Explore != 20 {
		t.Fatalf("%+v", s)
	}
}

func TestApproachSlowsWithDistance(t *testing.T) {
	prev := 1e9
	for d := 400.0; d >= 100; d -= 10 {
		v := Approach(120, true, d, 100)
		if v > prev+1e-9 || v > 120 || v < MinApproach-1e-9 {
			t.Fatalf("at %.0f mm: %.1f (prev %.1f)", d, v, prev)
		}
		if v > MinApproach && StopMM(v) > d-100+1e-6 {
			t.Fatalf("at %.0f mm: %.1f cannot stop in time", d, v)
		}
		prev = v
	}
	if Approach(120, true, 300, 100) != 120 || Approach(120, false, 50, 100) != 120 || Approach(120, true, 100, 100) != MinApproach {
		t.Fatal("ends")
	}
}

// 120 mm/s cruise on the live floor model: reaches cruise, ramps (no jump
// straight to full speed), never runs away.
func TestFastCruiseRampsUp(t *testing.T) {
	w := &wheel{brk: 0.45, roll: 0.15, k: 250}
	c := New(120)
	c.Accel = 400
	t0 := time.Unix(0, 0)
	c.Reset(t0)
	var vs []float64
	last, startAt := 0.0, -1
	for i := 1; i <= 800; i++ {
		now := t0.Add(time.Duration(i) * 5 * time.Millisecond)
		w.step(c.Update(now, w.pos), 0.005)
		if startAt < 0 && c.Moving() {
			startAt = i
		}
		if i%20 == 0 {
			vs = append(vs, (w.pos-last)/0.1)
			last = w.pos
		}
	}
	tail := vs[len(vs)-10:]
	for _, v := range tail {
		if v < 90 || v > 150 {
			t.Fatalf("cruise %.0f out of band: %v", v, vs)
		}
	}
	for i := 1; i < len(vs); i++ {
		if vs[i]-vs[i-1] > 60 { // > 600 mm/s^2 over 100 ms = a lurch
			t.Fatalf("lurch %.0f -> %.0f: %v", vs[i-1], vs[i], vs)
		}
	}
	// lowering the target applies at once
	c.SetTarget(30)
	if c.Cur() != 30 {
		t.Fatalf("cur %f", c.Cur())
	}
}
