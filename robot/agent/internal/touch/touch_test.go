package touch

import (
	"math"
	"math/rand"
	"testing"
	"time"
)

type run struct {
	d   Detector
	t   time.Time
	r   *rand.Rand
	evs []Event
}

func newRun() *run { return &run{t: time.Unix(1000, 0), r: rand.New(rand.NewSource(3))} }

// feed n samples at 50 Hz (the agent's control tick).
func (r *run) feed(n int, level func(i int) float64, chg bool) {
	for i := 0; i < n; i++ {
		v := level(i) + float64(r.r.Intn(7)-3)
		if e := r.d.Feed(r.t, uint16(v), chg); e != None {
			r.evs = append(r.evs, e)
		}
		r.t = r.t.Add(20 * time.Millisecond)
	}
}

func flat(v float64) func(int) float64 { return func(int) float64 { return v } }

func count(evs []Event, e Event) int {
	n := 0
	for _, x := range evs {
		if x == e {
			n++
		}
	}
	return n
}

func TestIdleNoEvents(t *testing.T) {
	r := newRun()
	r.feed(3000, flat(5461), true) // 60 s of the recorded idle level
	if len(r.evs) != 0 {
		t.Fatalf("idle events %v", r.evs)
	}
	if math.Abs(r.d.Baseline()-5461) > 5 {
		t.Fatalf("baseline %.1f", r.d.Baseline())
	}
}

func TestStrokePetting(t *testing.T) {
	r := newRun()
	r.feed(150, flat(5461), true)
	// a hand on the back: +120..+200 with strokes (~1.2 Hz swing) for 7 s
	r.feed(350, func(i int) float64 { return 5461 + 160 + 40*math.Sin(float64(i)*0.15) }, true)
	r.feed(100, flat(5461), true)
	if count(r.evs, Touched) != 1 || count(r.evs, PetStart) != 1 || count(r.evs, Released) != 1 {
		t.Fatalf("events %v", r.evs)
	}
	if count(r.evs, PetLevel) < 3 {
		t.Fatalf("levels %v", r.evs)
	}
	if r.d.Strokes() < 3 {
		t.Fatalf("strokes %d", r.d.Strokes())
	}
	if r.d.Touching() {
		t.Fatal("still touching")
	}
}

func TestTapIsNotPetting(t *testing.T) {
	r := newRun()
	r.feed(150, flat(5461), true)
	r.feed(10, flat(5461+150), true) // 200 ms tap
	r.feed(50, flat(5461), true)
	if count(r.evs, Touched) != 1 || count(r.evs, PetStart) != 0 || count(r.evs, Released) != 1 {
		t.Fatalf("events %v", r.evs)
	}
}

func TestSpikeAndChargerShiftIgnored(t *testing.T) {
	r := newRun()
	r.feed(150, flat(5461), true)
	r.feed(1, flat(5461+250), true) // one-sample glitch
	r.feed(50, flat(5461), true)
	r.feed(500, flat(5140), false) // level shift when leaving the charger
	r.feed(500, flat(5461), true)
	if len(r.evs) != 0 {
		t.Fatalf("events %v", r.evs)
	}
}

func TestHysteresisNoChatter(t *testing.T) {
	r := newRun()
	r.feed(150, flat(5461), true)
	// hovering between undetect (+45) and detect (+65) never flips
	r.feed(300, func(i int) float64 { return 5461 + 55 + 6*math.Sin(float64(i)) }, true)
	if len(r.evs) != 0 {
		t.Fatalf("events %v", r.evs)
	}
}
