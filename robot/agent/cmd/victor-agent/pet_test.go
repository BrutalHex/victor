package main

import (
	"math"
	"sync/atomic"
	"testing"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/spine"
)

func TestPettingFaceAndPurr(t *testing.T) {
	ui := &uiState{mode: "idle", frames: func() uint64 { return 0 }, log: func(string) {}}
	var purrs atomic.Int32
	p := &petting{ui: ui, play: func([]byte) bool { purrs.Add(1); return true }}
	now := time.Unix(100, 0)
	ui.now = func() time.Time { return now }
	feed := func(n int, lvl func(i int) float64) {
		for i := 0; i < n; i++ {
			fr := spine.Frame{Touch: uint16(lvl(i)), BatteryFlags: 1}
			p.feed(now, fr, nil)
			now = now.Add(20 * time.Millisecond)
		}
	}
	feed(150, func(int) float64 { return 5461 })
	if ui.currentMode() != "idle" {
		t.Fatalf("idle mode %s", ui.currentMode())
	}
	modes := map[string]bool{}
	for i := 0; i < 400; i++ {
		feed(1, func(int) float64 { return 5461 + 160 + 40*math.Sin(float64(i)*0.15) })
		if ui.currentMode() == "pet" {
			modes[ui.caption] = true
		}
	}
	if !modes["petting_lvl1"] || !modes["petting_bliss"] {
		t.Fatalf("pet faces %v", modes)
	}
	time.Sleep(20 * time.Millisecond) // purr goroutines
	if purrs.Load() < 2 || purrs.Load() > 4 {
		t.Fatalf("purrs %d", purrs.Load())
	}
	feed(50, func(int) float64 { return 5461 })
	if ui.currentMode() != "anim" || ui.caption != "petting_getout" {
		t.Fatalf("after release %s %s", ui.currentMode(), ui.caption)
	}
}

func TestPettingLeavesThinkingAlone(t *testing.T) {
	ui := &uiState{mode: "idle", frames: func() uint64 { return 0 }, log: func(string) {}}
	p := &petting{ui: ui, play: func([]byte) bool { return true }}
	now := time.Unix(100, 0)
	ui.now = func() time.Time { return now }
	ui.set("thinking", "", 0)
	for i := 0; i < 400; i++ {
		v := 5461.0
		if i > 150 {
			v += 180
		}
		p.feed(now, spine.Frame{Touch: uint16(v), BatteryFlags: 1}, nil)
		now = now.Add(20 * time.Millisecond)
		ui.set("thinking", "", 0) // hub keepalive
	}
	if ui.currentMode() != "thinking" {
		t.Fatalf("mode %s", ui.currentMode())
	}
}
