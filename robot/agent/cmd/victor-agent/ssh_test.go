package main

import (
	"compress/gzip"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/button"
	"github.com/BrutalHex/victor/robot/agent/internal/latch"
	"github.com/BrutalHex/victor/robot/agent/internal/spine"
	"github.com/BrutalHex/victor/robot/agent/internal/sshctl"
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

// Recorded frames with a real press patched in (touchLevel[1] = 0xFFFF for
// 300 ms) go through the same parse -> debounce -> latch path as the agent:
// one press for the hub, and with charge-latch.enabled absent (default) the
// latch never sees it, so a press can never toggle SSH.
func TestRealPressWakesButNeverTogglesSSH(t *testing.T) {
	f, err := os.Open("../../internal/spine/testdata/idle-oncharger.bin.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zr, _ := gzip.NewReader(f)
	raw, _ := io.ReadAll(zr)
	sshctl.LatchFlag = filepath.Join(t.TempDir(), "charge-latch.enabled") // absent
	var btn button.Debouncer
	m := latch.New()
	t0 := time.Unix(0, 0)
	toggles := 0
	for rep := 0; rep < 30; rep++ { // 30 x 80 frames x 20 ms = 48 s, one press per replay
		for i := 0; i+spine.DataRXSize <= len(raw); i += spine.DataRXSize {
			b := append([]byte(nil), raw[i:i+spine.DataRXSize]...)
			if i/spine.DataRXSize >= 20 && i/spine.DataRXSize < 35 {
				b[94], b[95] = 0xff, 0xff
			}
			fr, err := spine.ParsePacked(b)
			if err != nil {
				t.Fatal(err)
			}
			t0 = t0.Add(20 * time.Millisecond)
			btn.Feed(fr.Button, t0)
			if buttonLatch(m, latch.Sample{T: time.Duration(t0.UnixNano()), Button: fr.Button, OnCharger: true}, sshctl.LatchEnabled()) {
				toggles++
			}
		}
	}
	if btn.Presses != 30 || toggles != 0 {
		t.Fatalf("presses=%d toggles=%d (want 30, 0)", btn.Presses, toggles)
	}
}
