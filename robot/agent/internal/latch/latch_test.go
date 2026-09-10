package latch

import (
	"testing"
	"time"
)

func press(m *Machine, t0 *time.Duration, hold time.Duration, charger bool, lift float64) Result {
	*t0 += time.Millisecond
	m.Feed(Sample{T: *t0, Button: true, OnCharger: charger, LiftNorm: lift})
	*t0 += hold
	return m.Feed(Sample{T: *t0, Button: false, OnCharger: charger, LiftNorm: lift})
}

func settle(m *Machine, t0 *time.Duration, wait time.Duration, charger bool, lift float64) Result {
	*t0 += wait
	return m.Feed(Sample{T: *t0, Button: false, OnCharger: charger, LiftNorm: lift})
}

func phrase(m *Machine, t0 *time.Duration) Result {
	press(m, t0, 50*time.Millisecond, true, 0.0)
	press(m, t0, 50*time.Millisecond, true, 0.0)
	settle(m, t0, MultiClickGap, true, 0.0)
	settle(m, t0, 100*time.Millisecond, true, 0.95)
	settle(m, t0, LiftHold, true, 0.95)
	settle(m, t0, 100*time.Millisecond, true, 0.05)
	press(m, t0, 50*time.Millisecond, true, 0.05)
	press(m, t0, 50*time.Millisecond, true, 0.05)
	press(m, t0, 50*time.Millisecond, true, 0.05)
	return settle(m, t0, MultiClickGap, true, 0.05)
}

func TestHappyPathToggles(t *testing.T) {
	m := New()
	var now time.Duration
	if got := phrase(m, &now); got != ToggleSSH {
		t.Fatalf("want ToggleSSH, got %v phase=%s t=%s", got, m.Phase(), now)
	}
	if m.Phase() != Idle {
		t.Fatalf("should return to idle, got %s", m.Phase())
	}
}

func TestIgnoresOffCharger(t *testing.T) {
	m := New()
	var now time.Duration
	press(m, &now, 50*time.Millisecond, false, 0)
	press(m, &now, 50*time.Millisecond, false, 0)
	if got := settle(m, &now, MultiClickGap, false, 0); got != None || m.Phase() != Idle {
		t.Fatalf("off charger must be ignored, got %v %s", got, m.Phase())
	}
}

func TestIgnoresDriving(t *testing.T) {
	m := New()
	now := time.Duration(0)
	m.Feed(Sample{T: now, Button: true, OnCharger: true, Driving: true, LiftNorm: 0})
	now += 50 * time.Millisecond
	m.Feed(Sample{T: now, Button: false, OnCharger: true, Driving: true, LiftNorm: 0})
	if m.Phase() != Idle {
		t.Fatalf("driving must reset, phase=%s", m.Phase())
	}
}

func TestClickTooShortIgnored(t *testing.T) {
	m := New()
	var now time.Duration
	press(m, &now, 10*time.Millisecond, true, 0)
	press(m, &now, 50*time.Millisecond, true, 0)
	press(m, &now, 50*time.Millisecond, true, 0)
	settle(m, &now, MultiClickGap, true, 0)
	if m.Phase() != AwaitLiftTop {
		t.Fatalf("two valid clicks after bounce should count as double, phase=%s", m.Phase())
	}
}

func TestClickTooLongNotAClick(t *testing.T) {
	m := New()
	var now time.Duration
	press(m, &now, 400*time.Millisecond, true, 0)
	press(m, &now, 50*time.Millisecond, true, 0)
	settle(m, &now, MultiClickGap, true, 0)
	if m.Phase() != Idle {
		t.Fatalf("long hold is not a click, phase=%s", m.Phase())
	}
}

func TestLiftHoldTooShort(t *testing.T) {
	m := New()
	var now time.Duration
	press(m, &now, 50*time.Millisecond, true, 0)
	press(m, &now, 50*time.Millisecond, true, 0)
	settle(m, &now, MultiClickGap, true, 0)
	settle(m, &now, 100*time.Millisecond, true, 0.95)
	settle(m, &now, 100*time.Millisecond, true, 0.95)
	settle(m, &now, 100*time.Millisecond, true, 0.05)
	if m.Phase() == AwaitTriple {
		t.Fatal("must not accept a hold shorter than 400ms")
	}
}

func TestPhraseTimeout(t *testing.T) {
	m := New()
	var now time.Duration
	press(m, &now, 50*time.Millisecond, true, 0)
	press(m, &now, 50*time.Millisecond, true, 0)
	settle(m, &now, MultiClickGap, true, 0)
	settle(m, &now, 7*time.Second, true, 0.95)
	if m.Phase() != Idle {
		t.Fatalf("phrase >6s must reset, phase=%s", m.Phase())
	}
}

func TestTripleTooLate(t *testing.T) {
	m := New()
	var now time.Duration
	press(m, &now, 50*time.Millisecond, true, 0)
	press(m, &now, 50*time.Millisecond, true, 0)
	settle(m, &now, MultiClickGap, true, 0)
	settle(m, &now, 50*time.Millisecond, true, 0.95)
	settle(m, &now, LiftHold, true, 0.95)
	settle(m, &now, 50*time.Millisecond, true, 0.05)
	settle(m, &now, AfterBottomTriple+10*time.Millisecond, true, 0.05)
	press(m, &now, 50*time.Millisecond, true, 0.05)
	press(m, &now, 50*time.Millisecond, true, 0.05)
	press(m, &now, 50*time.Millisecond, true, 0.05)
	if got := settle(m, &now, MultiClickGap, true, 0.05); got != None {
		t.Fatalf("late triple must not toggle, got %v", got)
	}
}

func TestTwoPhrases(t *testing.T) {
	m := New()
	var now time.Duration
	if phrase(m, &now) != ToggleSSH {
		t.Fatal("first phrase")
	}
	now += time.Second
	if phrase(m, &now) != ToggleSSH {
		t.Fatal("second phrase")
	}
}
