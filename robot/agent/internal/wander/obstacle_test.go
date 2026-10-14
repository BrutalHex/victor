package wander

import (
	"math"
	"strings"
	"testing"
)

// room: axis-aligned walls around the start (x along the start heading).
// The ToF ray reads the nearest wall straight ahead; faint=true reports it
// only as ProxWeak (12 Oct live case: valid=false at 80-160 mm). Hitting a
// wall stops the body (wheels spin, encoders still count = slip).
type room struct {
	minX, maxX, minY, maxY float64
	faint                  bool
}

func (r room) ray(x, y, deg float64) float64 {
	h := deg * math.Pi / 180
	c, s := math.Cos(h), math.Sin(h)
	d := math.Inf(1)
	if c > 1e-6 {
		d = math.Min(d, (r.maxX-x)/c)
	} else if c < -1e-6 {
		d = math.Min(d, (r.minX-x)/c)
	}
	if s > 1e-6 {
		d = math.Min(d, (r.maxY-y)/s)
	} else if s < -1e-6 {
		d = math.Min(d, (r.minY-y)/s)
	}
	return d
}

func runRoom(t *testing.T, w *W, s *sim, r room, secs float64, each func(o Out)) {
	t.Helper()
	in := s.in
	for i := 0; i < int(secs*50); i++ {
		o := w.Tick(in)
		if o.PWM[2] != 0 {
			t.Fatal("lift moved")
		}
		if each != nil {
			each(o)
		}
		px, py := s.x, s.y
		in = s.step(o)
		// body is 50 mm from centre to nose: walls block it
		if s.x > r.maxX-50 || s.x < r.minX+50 || s.y > r.maxY-50 || s.y < r.minY+50 {
			s.x, s.y = px, py
		}
		d := r.ray(s.x, s.y, s.head) - 30 // ToF sits ~30 mm behind the nose
		in.ProxValid, in.ProxWeak, in.ProxMM = false, false, 0
		if d < 1200 {
			mm := uint16(math.Max(1, d))
			in.ProxMM = mm
			if r.faint {
				in.ProxWeak = true
			} else {
				in.ProxValid = true
			}
		}
		s.in = in
	}
}

func TestFaintObstacleStillTurnsAndResumes(t *testing.T) {
	s := newSim()
	r := room{minX: -2000, maxX: 300, minY: -2000, maxY: 2000, faint: true}
	w := New(11)
	w.Start(s.in, true)
	turned, after := false, 0.0
	var tx, ty float64
	runRoom(t, w, s, r, 30, func(o Out) {
		if o.State == Turn && !turned {
			turned = true
		}
		if turned && o.State == Drive {
			after = math.Max(after, math.Hypot(s.x-tx, s.y-ty))
		} else if !turned {
			tx, ty = s.x, s.y
		}
	})
	if !turned || after < 100 || !w.Active() {
		t.Fatalf("turned=%v drove %.0f mm after; active=%v why=%q", turned, after, w.Active(), w.Why)
	}
}

func TestObstacleTurnThenDriveResumes(t *testing.T) {
	s := newSim()
	r := room{minX: -3000, maxX: 260, minY: -3000, maxY: 3000}
	w := New(12)
	w.Start(s.in, true)
	var turns []float64
	resumed := false
	runRoom(t, w, s, r, 30, func(o Out) {
		if strings.HasPrefix(o.Event, "turn ") {
			turns = append(turns, w.target)
		}
		if len(turns) > 0 && o.State == Drive && s.x < 0 {
			resumed = true // drove back out, away from the wall
		}
	})
	if len(turns) == 0 || !resumed {
		t.Fatalf("turns=%v resumed=%v x=%.0f", turns, resumed, s.x)
	}
	for _, d := range turns {
		if a := math.Abs(d); a < 90 || a > 210 {
			t.Fatalf("turn %v outside 90-180 (210 boxed)", d)
		}
	}
}

func TestCornerEscapes(t *testing.T) {
	s := newSim()
	r := room{minX: -1500, maxX: 250, minY: -1500, maxY: 250}
	w := New(13)
	w.Start(s.in, true)
	far := 0.0
	runRoom(t, w, s, r, 60, func(o Out) { far = math.Max(far, math.Hypot(s.x, s.y)) })
	if !w.Active() || far < 400 {
		t.Fatalf("active=%v why=%q got only %.0f mm from the corner", w.Active(), w.Why, far)
	}
}

func TestBoxedInBiggerTurnsThenGivesUp(t *testing.T) {
	s := newSim()
	r := room{minX: -140, maxX: 140, minY: -140, maxY: 140}
	w := New(14)
	w.Start(s.in, true)
	n, big := 0, false
	runRoom(t, w, s, r, 120, func(o Out) {
		if strings.HasPrefix(o.Event, "turn ") || strings.HasPrefix(o.Event, "backing up") {
			n++
		}
		if o.State == Turn && math.Abs(w.target) >= 150 && w.blocked >= bigTurnAfter {
			big = true
		}
	})
	if w.Active() || w.Why != "boxed in" {
		t.Fatalf("active=%v why=%q", w.Active(), w.Why)
	}
	if !big || n < GiveUpAfter-1 {
		t.Fatalf("gave up too early: tries=%d big=%v", n, big)
	}
}

func TestBackupOnlyWithRearFloor(t *testing.T) {
	s := newSim()
	w := New(15)
	w.Start(s.in, true)
	in := s.in
	in.ProxValid, in.ProxMM = true, 40
	in.Cliffs[2], in.Cliffs[3] = 10, 10 // no floor behind
	for i := 0; i < 200; i++ {
		o := w.Tick(in)
		if o.PWM[0] < 0 && o.PWM[1] > 0 {
			t.Fatal("reversed with a rear cliff")
		}
		in.Now = in.Now.Add(20e6)
	}
}
