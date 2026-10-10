package wander

import (
	"math"
	"testing"
	"time"
)

// sim is a tiny body: wheel power -> speed -> encoders, and a 1-D world
// along the robot's heading (x mm travelled; wall / edge positions).
type sim struct {
	t      time.Time
	in     In
	x      float64 // mm along the start heading (2-D: x, y)
	y      float64
	head   float64 // deg
	wallAt float64 // mm, 0 = none
	edgeAt float64 // mm, 0 = none (front cliff when x >= edgeAt)
	stuck  bool
	encLf  float64
	encRf  float64
}

func newSim() *sim {
	s := &sim{t: time.Unix(1000, 0)}
	s.in = In{Now: s.t, CliffCal: true, Cliffs: [4]uint16{300, 300, 300, 300}, Thresh: [4]uint16{120, 120, 120, 120}}
	return s
}

func wheelSpeed(pwm int16) float64 {
	p := float64(pwm) / full
	if math.Abs(p) < 0.1 {
		return 0
	}
	return math.Copysign((math.Abs(p)-0.1)*250, p) // mm/s
}

// step applies out for 20 ms and returns the next input.
func (s *sim) step(o Out) In {
	dt := 0.02
	l, r := wheelSpeed(o.PWM[0]), -wheelSpeed(o.PWM[1])
	if s.stuck {
		l, r = 0, 0
	}
	s.encLf += l * dt / mmPerTick
	s.encRf -= r * dt / mmPerTick
	h := s.head * math.Pi / 180
	s.x += (l + r) / 2 * dt * math.Cos(h)
	s.y += (l + r) / 2 * dt * math.Sin(h)
	s.head += (r - l) / 2 * dt / halfTrackMM * 180 / math.Pi
	h = s.head * math.Pi / 180
	s.t = s.t.Add(20 * time.Millisecond)
	s.in.Now = s.t
	s.in.EncL, s.in.EncR = int32(s.encLf), int32(s.encRf)
	s.in.ProxValid, s.in.ProxMM = false, 0
	if s.wallAt > 0 && math.Cos(h) > 0.5 {
		d := (s.wallAt - s.x) / math.Cos(h)
		if d < 1200 {
			s.in.ProxValid, s.in.ProxMM = true, uint16(math.Max(1, d))
		}
	}
	// front cliff sensors ~30 mm ahead of the centre, rear ~30 mm behind
	front := s.x + 30*math.Cos(h)
	rear := s.x - 30*math.Cos(h)
	s.in.Cliffs = [4]uint16{300, 300, 300, 300}
	if s.edgeAt > 0 && front >= s.edgeAt {
		s.in.Cliffs[0], s.in.Cliffs[1] = 10, 10
	}
	if s.edgeAt > 0 && rear >= s.edgeAt {
		s.in.Cliffs[2], s.in.Cliffs[3] = 10, 10
	}
	return s.in
}

func run(t *testing.T, w *W, s *sim, secs float64, each func(i int, o Out)) {
	t.Helper()
	in := s.in
	for i := 0; i < int(secs*50); i++ {
		o := w.Tick(in)
		if o.PWM[2] != 0 {
			t.Fatal("lift moved")
		}
		if each != nil {
			each(i, o)
		}
		in = s.step(o)
	}
}

func TestRefusals(t *testing.T) {
	s := newSim()
	w := New(1)
	if w.Start(s.in, false) != "explore flag off" {
		t.Fatal(w.Why)
	}
	cases := map[string]func(*In){
		"on charger":                   func(i *In) { i.OnCharger = true },
		"cliff sensors not calibrated": func(i *In) { i.CliffCal = false },
		"picked up":                    func(i *In) { i.Pickup = true },
		"low battery":                  func(i *In) { i.LowBattery = true },
		"at an edge":                   func(i *In) { i.Cliffs[1] = 20 },
	}
	for want, mod := range cases {
		in := s.in
		mod(&in)
		if got := w.Start(in, true); got != want || w.Active() {
			t.Fatalf("%s: got %q", want, got)
		}
	}
	if w.Start(s.in, true) != "" || !w.Active() {
		t.Fatal("open floor should start")
	}
}

func TestSlowDriveOpenFloor(t *testing.T) {
	s := newSim()
	w := New(2)
	w.Start(s.in, true)
	maxV := 0.0
	lastX := 0.0
	run(t, w, s, 30, func(i int, o Out) {
		if i%25 == 0 {
			v := math.Abs(s.x-lastX) * 2
			if o.State == Drive && v > maxV {
				maxV = v
			}
			lastX = s.x
		}
	})
	if s.x < 150 {
		t.Fatalf("barely moved: %.0f mm", s.x)
	}
	if maxV > 65 {
		t.Fatalf("too fast: %.0f mm/s", maxV)
	}
}

func TestObstacleTurnsAway(t *testing.T) {
	s := newSim()
	s.wallAt = 220
	w := New(3)
	w.Start(s.in, true)
	turned := false
	run(t, w, s, 20, func(i int, o Out) {
		if s.in.ProxValid && s.in.ProxMM < ObstacleMM-15 && o.PWM[0] > 0 && o.PWM[1] < 0 {
			t.Fatalf("driving forward at %d mm", s.in.ProxMM)
		}
		if o.State == Turn {
			turned = true
		}
	})
	if !turned || s.x > s.wallAt-ObstacleMM+25 {
		t.Fatalf("turned=%v x=%.0f", turned, s.x)
	}
}

func TestCliffStopsThenOnlyReverses(t *testing.T) {
	s := newSim()
	s.edgeAt = 120
	w := New(4)
	w.Start(s.in, true)
	sawCliff, backed := false, false
	maxFront := 0.0
	run(t, w, s, 20, func(i int, o Out) {
		if f := s.x + 30*math.Cos(s.head*math.Pi/180); f > maxFront {
			maxFront = f
		}
		front := s.in.Cliffs[0] < 40
		if front {
			sawCliff = true
			if o.PWM[0] > 0 || o.PWM[1] < 0 { // any forward wheel power over an edge
				t.Fatalf("forward power at the edge: %v state=%s", o.PWM, o.State)
			}
			if o.PWM[0] < 0 {
				backed = true
			}
		}
	})
	if !sawCliff || !backed {
		t.Fatalf("cliff=%v backed=%v", sawCliff, backed)
	}
	if maxFront > s.edgeAt+5 { // one 20 ms tick at 40 mm/s is ~1 mm
		t.Fatalf("front sensor went %.1f mm past the edge", maxFront-s.edgeAt)
	}
}

func TestCliffFrontAndRearHalts(t *testing.T) {
	s := newSim()
	w := New(5)
	w.Start(s.in, true)
	run(t, w, s, 2, nil)
	s.in.Cliffs = [4]uint16{10, 10, 10, 10}
	s.in.Thresh = [4]uint16{120, 120, 120, 120}
	o := w.Tick(s.in)
	if o.State != Halt || o.PWM != ([4]int16{}) {
		t.Fatalf("%+v", o)
	}
	for i := 0; i < 100; i++ {
		s.in.Now = s.in.Now.Add(20 * time.Millisecond)
		if o = w.Tick(s.in); o.PWM != ([4]int16{}) {
			t.Fatal("moved while halted")
		}
	}
}

func TestHardStopsEndSession(t *testing.T) {
	for name, mod := range map[string]func(*In){
		"picked up":   func(i *In) { i.Pickup = true },
		"fall":        func(i *In) { i.Fall = true },
		"low battery": func(i *In) { i.LowBattery = true },
		"on charger":  func(i *In) { i.OnCharger = true },
		"hub gone":    func(i *In) { i.HubGone = true },
	} {
		s := newSim()
		w := New(6)
		w.Start(s.in, true)
		run(t, w, s, 3, nil)
		in := s.in
		mod(&in)
		o := w.Tick(in)
		if w.Active() || o.PWM != ([4]int16{}) || w.Why != name {
			t.Fatalf("%s: %+v why=%q", name, o, w.Why)
		}
	}
}

func TestStallTurnsAndBusyHolds(t *testing.T) {
	s := newSim()
	s.stuck = true
	w := New(7)
	w.Start(s.in, true)
	sawStall := false
	run(t, w, s, 6, func(i int, o Out) {
		if o.Event == "turn stall" {
			sawStall = true
		}
	})
	if !sawStall {
		t.Fatal("no stall detection")
	}
	s.stuck = false
	in := s.in
	in.Busy = true
	for i := 0; i < 50; i++ {
		in.Now = in.Now.Add(20 * time.Millisecond)
		if o := w.Tick(in); o.PWM != ([4]int16{}) || !w.Active() {
			t.Fatal("busy must hold still but keep the session")
		}
	}
}

func TestStopAndSessionCap(t *testing.T) {
	s := newSim()
	w := New(8)
	w.Start(s.in, true)
	run(t, w, s, 2, nil)
	w.Stop("voice stop")
	if o := w.Tick(s.in); w.Active() || o.PWM != ([4]int16{}) {
		t.Fatal("stop")
	}
	w.Start(s.in, true)
	in := s.in
	in.Now = in.Now.Add(SessionMax + time.Second)
	w.Tick(in)
	if w.Active() || w.Why != "session time cap" {
		t.Fatal(w.Why)
	}
}
