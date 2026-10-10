package wander

import (
	"github.com/BrutalHex/victor/robot/agent/internal/drivectl"
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
	// brk > 0: tread static friction like the live floor (10 Oct): a wheel
	// only starts at |power| >= brk, then rolls at (|p|-0.15)*160 mm/s.
	brk        float64
	movL, movR bool
	// inertia: real wheel speed follows the command, slowing at only
	// drivectl.BrakeMMps2 and speeding up at <= 1500 mm/s^2.
	inertia bool
	vL, vR  float64
}

func follow(v, want, dt float64) float64 {
	a := 1500.0
	if math.Abs(want) < math.Abs(v) || want*v < 0 {
		a = drivectl.BrakeMMps2
	}
	if d := want - v; math.Abs(d) <= a*dt {
		return want
	} else {
		return v + math.Copysign(a*dt, d)
	}
}

func (s *sim) wheel(pwm int16, moving *bool) float64 {
	if s.brk == 0 {
		return wheelSpeed(pwm)
	}
	p := float64(pwm) / full
	a := math.Abs(p)
	if !*moving && a >= s.brk {
		*moving = true
	}
	if !*moving || a <= 0.15 {
		*moving = false
		return 0
	}
	return math.Copysign((a-0.15)*160, p)
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
	l, r := s.wheel(o.PWM[0], &s.movL), -s.wheel(o.PWM[1], &s.movR)
	if s.stuck {
		l, r = 0, 0
	}
	if s.inertia {
		s.vL, s.vR = follow(s.vL, l, dt), follow(s.vR, r, dt)
		l, r = s.vL, s.vR
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
	cr := drivectl.Cfg.Explore
	if maxV > cr*1.3 {
		t.Fatalf("too fast: %.0f mm/s (cruise %.0f)", maxV, cr)
	}
	if maxV < cr*0.75 {
		t.Fatalf("still slow: %.0f mm/s (cruise %.0f)", maxV, cr)
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
	if maxFront > s.edgeAt+5 { // one 20 ms tick at 120 mm/s is ~2.4 mm
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

// Live 10 Oct: treads needed more than the old fixed 0.24-0.38 power to break
// away; the robot only twitched. The shared controller must drive and turn.
func TestBreakawayFloorDrivesAndTurns(t *testing.T) {
	s := newSim()
	s.brk = 0.45
	w := New(7)
	w.Start(s.in, true)
	maxV, lastX, turned := 0.0, 0.0, 0.0
	h0 := 0.0
	run(t, w, s, 30, func(i int, o Out) {
		if i%25 == 0 {
			v := math.Hypot(s.x-lastX, 0) * 2
			if o.State == Drive && v > maxV {
				maxV = v
			}
			lastX = s.x
		}
		if o.State == Turn && math.Abs(s.head-h0) > turned {
			turned = math.Abs(s.head - h0)
		}
		if o.State != Turn {
			h0 = s.head
		}
	})
	if math.Hypot(s.x, s.y) < 150 {
		t.Fatalf("barely moved: x=%.0f y=%.0f", s.x, s.y)
	}
	if maxV > drivectl.Cfg.Explore*1.3 {
		t.Fatalf("too fast after breakaway: %.0f mm/s", maxV)
	}
}

func TestBreakawayTurnCompletes(t *testing.T) {
	s := newSim()
	s.brk = 0.45
	s.wallAt = 150 // obstacle right away -> turn
	w := New(8)
	w.Start(s.in, true)
	maxTurn := 0.0
	run(t, w, s, 8, func(i int, o Out) {
		if math.Abs(s.head) > maxTurn {
			maxTurn = math.Abs(s.head)
		}
	})
	if maxTurn < 80 {
		t.Fatalf("turn only reached %.0f deg", maxTurn)
	}
}

func TestSingleNoisyProxReturnIgnored(t *testing.T) {
	s := newSim()
	w := New(9)
	w.Start(s.in, true)
	in := s.in
	for i := 0; i < 400; i++ {
		if i%10 == 0 { // one close return every 200 ms, never twice in a row
			in.ProxValid, in.ProxMM = true, 30
		}
		o := w.Tick(in)
		if o.State == Turn {
			t.Fatalf("turned on an isolated noisy return at tick %d", i)
		}
		in = s.step(o)
	}
}

// At full cruise with a body that cannot stop instantly, the front cliff
// sensor may not overrun the edge by more than CliffMarginMM, the reverse
// starts on the very tick the edge is seen, and the back-off clears it.
func TestCliffStopWithinBrakingDistanceAtCruise(t *testing.T) {
	for _, cruise := range []float64{80, 120, drivectl.MaxMMps} {
		old := drivectl.Cfg
		drivectl.Cfg.Explore = cruise
		s := newSim()
		s.inertia = true
		s.edgeAt = 400 // long run-up: reaches full cruise first
		w := New(4)
		drivectl.Cfg = old
		w.Start(s.in, true)
		maxFront, vAtEdge, kicked := 0.0, -1.0, false
		lastX := s.x
		run(t, w, s, 20, func(i int, o Out) {
			if f := s.x + 30*math.Cos(s.head*math.Pi/180); f > maxFront {
				maxFront = f
			}
			if s.in.Cliffs[0] < 40 && vAtEdge < 0 {
				vAtEdge = (s.x - lastX) / 0.02
				kicked = o.PWM[0] < 0 && o.PWM[1] > 0
			}
			lastX = s.x
		})
		if vAtEdge < 0 {
			t.Fatalf("cruise %.0f: never reached the edge", cruise)
		}
		if !kicked {
			t.Fatalf("cruise %.0f: no reverse brake on the cliff tick", cruise)
		}
		over := maxFront - s.edgeAt
		if over > drivectl.CliffMarginMM || over > drivectl.StopMM(vAtEdge)+3 {
			t.Fatalf("cruise %.0f: hit edge at %.0f mm/s, front sensor overran %.1f mm (model %.1f, margin %.0f)",
				cruise, vAtEdge, over, drivectl.StopMM(vAtEdge), drivectl.CliffMarginMM)
		}
		t.Logf("cruise %.0f: %.0f mm/s at the edge, overran %.1f mm (model %.1f)", cruise, vAtEdge, over, drivectl.StopMM(vAtEdge))
		if vAtEdge < cruise*0.6 {
			t.Fatalf("cruise %.0f: only %.0f mm/s at the edge (test not at speed)", cruise, vAtEdge)
		}
		if s.in.Cliffs[0] < 40 {
			t.Fatalf("cruise %.0f: still over the edge after back-off", cruise)
		}
	}
}

// Speed scales with ToF distance: arriving at the obstacle distance slowly,
// never touching the wall even with inertia.
func TestSlowsAsObstacleNears(t *testing.T) {
	s := newSim()
	s.inertia = true
	s.wallAt = 700
	w := New(3)
	w.Start(s.in, true)
	lastX, turned := s.x, false
	run(t, w, s, 20, func(i int, o Out) {
		v := (s.x - lastX) / 0.02
		lastX = s.x
		if turned || o.State == Turn {
			turned = true
			return
		}
		if s.in.ProxValid && s.in.ProxMM < ObstacleMM+40 && v > 70 {
			t.Fatalf("%.0f mm/s at %d mm from the wall", v, s.in.ProxMM)
		}
	})
	if !turned {
		t.Fatal("never turned at the wall")
	}
	if s.wallAt-s.x < 40 {
		t.Fatalf("got within %.0f mm of the wall", s.wallAt-s.x)
	}
}

func TestBackMMCoversStoppingDistance(t *testing.T) {
	for _, v := range []float64{40, 120, drivectl.MaxMMps} {
		if BackMM(v) < drivectl.StopMM(v)+30 || BackMM(v) < 40 {
			t.Fatalf("%.0f: back %f", v, BackMM(v))
		}
	}
}
