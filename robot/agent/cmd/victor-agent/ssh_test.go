package main

import (
	"testing"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/latch"
)

// replay the full CHARGE-LATCH gesture through buttonLatch, plus a button that
// reads "pressed" on every frame (what this robot reports, 10 Oct 2026).
func gesture(enabled bool) (toggles int) {
	m := latch.New()
	now := time.Duration(0)
	feed := func(dt time.Duration, button bool, lift float64) {
		now += dt
		if buttonLatch(m, latch.Sample{T: now, Button: button, OnCharger: true, LiftNorm: lift}, enabled) {
			toggles++
		}
	}
	clk := func(lift float64) {
		feed(50*time.Millisecond, true, lift)
		feed(50*time.Millisecond, false, lift)
	}
	clk(0)
	clk(0)
	feed(latch.MultiClickGap, false, 0)
	feed(80*time.Millisecond, false, 0.95)
	feed(latch.LiftHold, false, 0.95)
	feed(80*time.Millisecond, false, 0.05)
	clk(0.05)
	clk(0.05)
	clk(0.05)
	feed(latch.MultiClickGap, false, 0.05)
	for i := 0; i < 3000; i++ { // 5 min of an always-pressed button, lift moving
		feed(100*time.Millisecond, true, float64(i%20)/20)
	}
	return toggles
}

func TestButtonNoLongerTogglesSSH(t *testing.T) {
	if n := gesture(false); n != 0 {
		t.Fatalf("button toggled SSH %d times with the latch disabled", n)
	}
}

func TestLatchCodeStillWorksWhenRearmed(t *testing.T) {
	if n := gesture(true); n < 1 {
		t.Fatal("re-armed latch did not toggle on the full gesture")
	}
}
