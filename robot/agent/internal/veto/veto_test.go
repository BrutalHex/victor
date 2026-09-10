package veto

import (
	"testing"
	"time"
)

func base() Input {
	return Input{
		Cliffs:       [4]uint16{400, 410, 390, 395},
		Thresh:       [4]uint16{160, 164, 156, 158},
		Calibrated:   true,
		ProxMM:       120,
		HasHeartbeat: true,
		HeartbeatAge: 40 * time.Millisecond,
		BattMV:       3800,
	}
}

func TestClear(t *testing.T) {
	if Check(base()) != Clear {
		t.Fatal(Check(base()))
	}
}

func TestFrontCliff(t *testing.T) {
	in := base()
	in.Cliffs[0] = 10
	if Check(in) != Cliff {
		t.Fatalf("got %s", Check(in))
	}
	in = base()
	in.Calibrated = false
	in.Thresh = [4]uint16{}
	in.Cliffs[0] = 0
	if Check(in) != Cliff {
		t.Fatalf("void front without cal got %s", Check(in))
	}
}

func TestHeartbeatMiss(t *testing.T) {
	in := base()
	in.HeartbeatAge = 300 * time.Millisecond
	if Check(in) != HeartbeatMiss {
		t.Fatalf("got %s", Check(in))
	}
	in.HasHeartbeat = false
	in.HeartbeatAge = 0
	if Check(in) != HeartbeatMiss {
		t.Fatal("no hub must veto")
	}
}

func TestBattery(t *testing.T) {
	in := base()
	in.BattMV = 3300
	if Check(in) != Battery {
		t.Fatalf("got %s", Check(in))
	}
}

func TestToFOnlyWhenForward(t *testing.T) {
	in := base()
	in.ProxMM = 0
	if Check(in) != Clear {
		t.Fatalf("idle tof got %s", Check(in))
	}
	in.Forward = true
	if Check(in) != ToF {
		t.Fatalf("forward tof got %s", Check(in))
	}
}

func TestPickupAllCliffs(t *testing.T) {
	in := base()
	in.Cliffs = [4]uint16{5, 5, 5, 5}
	if Check(in) != Pickup {
		t.Fatalf("got %s", Check(in))
	}
	in.OnCharger = true
	in.Cliffs = [4]uint16{5, 5, 5, 5}
	// on charger, all-void is not pickup; front cliff still fires
	if r := Check(in); r != Cliff {
		t.Fatalf("on charger got %s", r)
	}
}

func TestTTL(t *testing.T) {
	in := base()
	in.HasCommand = true
	in.CommandAge = 500 * time.Millisecond
	if Check(in) != TTL {
		t.Fatalf("got %s", Check(in))
	}
}

func TestClassicalWinsOrder(t *testing.T) {
	in := base()
	in.BattMV = 3000
	in.Cliffs[0] = 0
	if Check(in) != Battery {
		t.Fatal("battery must win over cliff")
	}
}
