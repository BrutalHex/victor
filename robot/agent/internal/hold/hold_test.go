package hold

import (
	"testing"
	"time"
)

func TestParse(t *testing.T) {
	for in, want := range map[string]time.Duration{"hold:700": 700 * time.Millisecond, "hold:99999": Max, "release": 0} {
		if d, ok := Parse(in); !ok || d != want {
			t.Fatalf("%s -> %v %v", in, d, ok)
		}
	}
	for _, bad := range []string{"hold:", "hold:-5", "hold:x", "backup", "plan:drive 50"} {
		if _, ok := Parse(bad); ok {
			t.Fatalf("%s parsed", bad)
		}
	}
}

func TestSoftBrakeThenZero(t *testing.T) {
	var c Ctl
	t0 := time.Now()
	if !c.Hold(t0, time.Second, [4]int16{20000, -20000, 0, 0}) {
		t.Fatal("not started")
	}
	l, r := c.Brake(t0)
	if l != 20000 || r != -20000 {
		t.Fatalf("brake starts at last power, got %d %d", l, r)
	}
	l, r = c.Brake(t0.Add(75 * time.Millisecond))
	if l < 9000 || l > 11000 || r > -9000 || r < -11000 {
		t.Fatalf("half way should be ~half power, got %d %d", l, r)
	}
	if l, r = c.Brake(t0.Add(BrakeFor)); l != 0 || r != 0 {
		t.Fatalf("after the brake: %d %d", l, r)
	}
}

func TestExtendReleaseExpire(t *testing.T) {
	var c Ctl
	t0 := time.Now()
	c.Hold(t0, 500*time.Millisecond, [4]int16{})
	if c.Hold(t0.Add(400*time.Millisecond), time.Second, [4]int16{5000, -5000}) {
		t.Fatal("refresh must not restart the hold (or the brake)")
	}
	if !c.Active(t0.Add(1300 * time.Millisecond)) {
		t.Fatal("extended hold ended early")
	}
	if d := c.Release(t0.Add(1200 * time.Millisecond)); d != 1200*time.Millisecond {
		t.Fatalf("held %v", d)
	}
	if c.Active(t0.Add(1300*time.Millisecond)) || c.Release(t0) != 0 {
		t.Fatal("release did not end it")
	}
	c.Hold(t0, 700*time.Millisecond, [4]int16{})
	if _, ok := c.Expired(t0.Add(600 * time.Millisecond)); ok {
		t.Fatal("expired early")
	}
	d, ok := c.Expired(t0.Add(900 * time.Millisecond))
	if !ok || d != 700*time.Millisecond {
		t.Fatalf("expiry %v %v", d, ok)
	}
	if _, ok := c.Expired(t0.Add(time.Second)); ok {
		t.Fatal("expiry reported twice")
	}
}

func TestHoldNeverLongerThanMax(t *testing.T) {
	var c Ctl
	t0 := time.Now()
	c.Hold(t0, time.Hour, [4]int16{})
	if c.Active(t0.Add(Max + time.Millisecond)) {
		t.Fatal("a hold must expire on its own")
	}
}

func TestRampFlags(t *testing.T) {
	var r Ramp
	t0 := time.Now()
	if r.Feed(t0, [4]int16{}) {
		t.Fatal("still -> no ramp")
	}
	if !r.Feed(t0, [4]int16{12000, -12000}) {
		t.Fatal("start is a ramp")
	}
	if !r.Feed(t0.Add(100*time.Millisecond), [4]int16{12500, -12500}) {
		t.Fatal("still inside the ramp window")
	}
	if r.Feed(t0.Add(250*time.Millisecond), [4]int16{13000, -13000}) {
		t.Fatal("speed-loop trim is not a ramp")
	}
	if !r.Feed(t0.Add(300*time.Millisecond), [4]int16{0, 0}) {
		t.Fatal("stop is a ramp")
	}
	if !r.Feed(t0.Add(900*time.Millisecond), [4]int16{0, 0, 0, 8192}) {
		t.Fatal("head start is a ramp")
	}
}
