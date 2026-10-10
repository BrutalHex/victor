package main

import (
	"strings"
	"testing"
	"time"
)

type fakeClock struct{ t time.Time }

func (c *fakeClock) now() time.Time      { return c.t }
func (c *fakeClock) add(d time.Duration) { c.t = c.t.Add(d) }
func newUI() (*uiState, *fakeClock, *[]string) {
	c := &fakeClock{t: time.Date(2026, 10, 9, 17, 0, 0, 0, time.UTC)}
	var logs []string
	u := &uiState{mode: "idle", now: c.now, log: func(s string) { logs = append(logs, s) }, frames: func() uint64 { return 0 }}
	return u, c, &logs
}

func TestThinkingClearsWhenPlaybackStarts(t *testing.T) {
	u, c, logs := newUI()
	u.set("thinking", "", 0)
	c.add(3 * time.Second)
	if !u.thinking() {
		t.Fatal("thinking should be on during the turn")
	}
	u.speakStart()
	if u.thinking() {
		t.Fatal("playback must end thinking")
	}
	u.set("idle", "", 0) // the hub's idle| after the speech is a no-op
	if len(*logs) != 2 || !strings.Contains((*logs)[1], "why=speak") {
		t.Fatalf("logs %q", *logs)
	}
}

func TestKeepaliveCoversSlowTTS(t *testing.T) {
	u, c, _ := newUI()
	u.set("thinking", "", 0)
	start := u.thinkStart
	for i := 0; i < 9; i++ { // 36 s of refreshes every 4 s
		c.add(4 * time.Second)
		u.set("thinking", "", 0)
		if !u.thinking() {
			t.Fatalf("dropped at %d", i)
		}
	}
	if !u.thinkStart.Equal(start) {
		t.Fatal("refresh restarted the animation phase")
	}
}

func TestStaleThinkingExpires(t *testing.T) {
	u, c, logs := newUI()
	u.set("thinking", "", 0)
	c.add(thinkStale - time.Second)
	if !u.thinking() {
		t.Fatal("too early")
	}
	c.add(2 * time.Second)
	if u.thinking() {
		t.Fatal("stale thinking must clear (hub restart / lost idle)")
	}
	if !strings.Contains((*logs)[len(*logs)-1], "why=stale") {
		t.Fatalf("logs %q", *logs)
	}
}

func TestIdleAndErrorClear(t *testing.T) {
	u, _, _ := newUI()
	u.set("thinking", "", 0)
	u.set("idle", "", 0)
	if u.thinking() {
		t.Fatal("idle must clear thinking")
	}
}

func TestNameCardSurvivesSpeakStart(t *testing.T) {
	u, _, _ := newUI()
	u.set("thinking", "", 0)
	u.set("name", "ANN", 3*time.Second)
	u.speakStart()
	if u.mode != "name" {
		t.Fatalf("mode %s", u.mode)
	}
}
