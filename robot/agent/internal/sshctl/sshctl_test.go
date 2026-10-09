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
