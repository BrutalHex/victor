package button

import (
	"testing"
	"time"
)

func feed(d *Debouncer, t0 *time.Time, raw bool, ms int) int {
	n := 0
	for i := 0; i < ms/20; i++ {
		*t0 = t0.Add(20 * time.Millisecond)
		if d.Feed(raw, *t0) {
			n++
		}
	}
	return n
}

func TestSinglePressCountsOnce(t *testing.T) {
	var d Debouncer
	t0 := time.Unix(0, 0)
	feed(&d, &t0, false, 200)
	if n := feed(&d, &t0, true, 300); n != 1 {
		t.Fatalf("press -> %d events", n)
	}
	if n := feed(&d, &t0, false, 300); n != 0 || d.Presses != 1 {
		t.Fatalf("release -> %d, presses %d", n, d.Presses)
	}
	feed(&d, &t0, true, 100)
	feed(&d, &t0, false, 100)
	if d.Presses != 2 {
		t.Fatalf("second press: %d", d.Presses)
	}
}

func TestBounceAndGlitchesIgnored(t *testing.T) {
	var d Debouncer
	t0 := time.Unix(0, 0)
	feed(&d, &t0, false, 200)
	for i := 0; i < 20; i++ { // single-sample glitches (20 ms < 40 ms)
		feed(&d, &t0, true, 20)
		feed(&d, &t0, false, 60)
	}
	if d.Presses != 0 {
		t.Fatalf("glitches -> %d presses", d.Presses)
	}
	// contact bounce inside one real press still gives one press
	for _, v := range []bool{true, false, true, false, true} {
		feed(&d, &t0, v, 20)
	}
	feed(&d, &t0, true, 200)
	feed(&d, &t0, false, 200)
	if d.Presses != 1 {
		t.Fatalf("bouncy press -> %d", d.Presses)
	}
}

func TestStuckPressedGivesNoStream(t *testing.T) {
	var d Debouncer
	t0 := time.Unix(0, 0)
	if n := feed(&d, &t0, true, 10*60*1000); n != 0 { // pressed from the start: not a press
		t.Fatalf("stuck from start -> %d", n)
	}
	feed(&d, &t0, false, 200)
	if n := feed(&d, &t0, true, 10*60*1000); n != 1 {
		t.Fatalf("held 10 min -> %d presses", n)
	}
}
