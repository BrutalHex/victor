package sshctl

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestWatchdogDue(t *testing.T) {
	cases := []struct {
		off     time.Duration
		hb, chg bool
		want    bool
	}{
		{25 * time.Hour, true, true, true},
		{23 * time.Hour, true, true, false},
		{25 * time.Hour, false, true, false},
		{25 * time.Hour, true, false, false},
	}
	for _, c := range cases {
		if got := WatchdogDue(c.off, c.hb, c.chg); got != c.want {
			t.Errorf("WatchdogDue(%v,%v,%v)=%v want %v", c.off, c.hb, c.chg, got, c.want)
		}
	}
}

func TestOffForSurvivesReboot(t *testing.T) {
	now := time.Date(2026, 10, 9, 12, 0, 0, 0, time.UTC)
	// Agent restarted 1 min ago, flag was written 30 h ago.
	if d := OffFor(now, now.Add(-time.Minute), now.Add(-30*time.Hour)); d != 30*time.Hour {
		t.Fatalf("got %v", d)
	}
	// Clock unset at boot (1970 mtime) falls back to in-process timer.
	if d := OffFor(now, now.Add(-time.Minute), time.Unix(0, 0)); d != time.Minute {
		t.Fatalf("got %v", d)
	}
	// Future mtime is ignored.
	if d := OffFor(now, time.Time{}, now.Add(time.Hour)); d != 0 {
		t.Fatalf("got %v", d)
	}
}

func TestEnabledDefaultsOnAndPersists(t *testing.T) {
	dir := t.TempDir()
	c := &Controller{Flag: filepath.Join(dir, "ssh.enabled")}
	if !c.Enabled() {
		t.Fatal("missing flag must mean SSH ON (first boot)")
	}
	if err := os.WriteFile(c.Flag, []byte("0\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if c.Enabled() {
		t.Fatal("flag 0 must mean SSH OFF")
	}
	if c.OffSince().IsZero() {
		t.Fatal("OffSince should read flag mtime")
	}
}

func TestVoiceActions(t *testing.T) {
	for name, want := range map[string][3]bool{
		"ssh_on": {true, false, true}, "ssh_off": {false, false, true}, "ssh_status": {false, true, true},
		"ssh_toggle": {false, false, false}, "explore": {false, false, false}, "": {false, false, false},
	} {
		on, st, ok := VoiceAction(name)
		if [3]bool{on, st, ok} != want {
			t.Errorf("%q -> %v %v %v", name, on, st, ok)
		}
	}
}

// Voice off persists (flag file) and the 24 h watchdog still re-enables.
func TestVoiceOffPersistsAndWatchdogStillWorks(t *testing.T) {
	dir := t.TempDir()
	c := &Controller{Flag: filepath.Join(dir, "ssh.enabled"), Socket: "victor-test-none.socket", Dropbear: "victor-test-none.service"}
	if !c.Enabled() {
		t.Fatal("missing flag must mean SSH ON (first boot)")
	}
	_ = c.Set(false) // no units on the test box: Apply errors, the flag is still written
	again := &Controller{Flag: c.Flag}
	if again.Enabled() {
		t.Fatal("voice off did not persist")
	}
	old := time.Now().Add(-25 * time.Hour)
	if err := os.Chtimes(c.Flag, old, old); err != nil {
		t.Fatal(err)
	}
	off := OffFor(time.Now(), time.Time{}, again.OffSince())
	if !WatchdogDue(off, true, true) {
		t.Fatalf("watchdog not due after %v off", off)
	}
	if WatchdogDue(off, false, true) || WatchdogDue(off, true, false) {
		t.Fatal("watchdog rule changed")
	}
	_ = again.Set(true)
	if !(&Controller{Flag: c.Flag}).Enabled() {
		t.Fatal("voice on did not persist")
	}
}

func TestLatchDisabledByDefault(t *testing.T) {
	old := LatchFlag
	defer func() { LatchFlag = old }()
	LatchFlag = filepath.Join(t.TempDir(), "charge-latch.enabled")
	if LatchEnabled() {
		t.Fatal("latch must default off")
	}
	_ = os.WriteFile(LatchFlag, nil, 0644)
	if !LatchEnabled() {
		t.Fatal("flag file should re-arm it")
	}
}
