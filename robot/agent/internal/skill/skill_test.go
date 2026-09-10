package skill

import "testing"

func TestCreepIsCapped(t *testing.T) {
	vx, _ := CreepForward.Twist()
	if vx < 40 || vx > 60 {
		t.Fatalf("vx %v", vx)
	}
}

func TestWheelsZeroWithoutPermit(t *testing.T) {
	m := PWM(CreepForward, false)
	if m[0] != 0 || m[1] != 0 {
		t.Fatalf("%v", m)
	}
}

func TestForwardDifferential(t *testing.T) {
	m := PWM(CreepForward, true)
	if m[0] <= 0 || m[1] >= 0 {
		t.Fatalf("expected +right -left, got %v", m)
	}
	if abs(m[0]) > creepPWM || abs(m[1]) > creepPWM {
		t.Fatalf("pwm cap %v", m)
	}
}

func TestAllowWheelsGates(t *testing.T) {
	cases := []struct {
		explore, clear, charger, want bool
		name                          string
	}{
		{false, true, false, false, "no flag"},
		{true, true, true, false, "on charger"},
		{true, false, false, false, "veto"},
		{true, true, false, true, "floor wander"},
		{false, true, true, false, "no flag on charger"},
	}
	for _, c := range cases {
		got := AllowWheels(c.explore, c.clear, c.charger)
		if got != c.want {
			t.Fatalf("%s: got %v want %v", c.name, got, c.want)
		}
		pwm := PWM(CreepForward, got)
		if !c.want && (pwm[0] != 0 || pwm[1] != 0) {
			t.Fatalf("%s leaked wheel pwm %v", c.name, pwm)
		}
		if c.want && (pwm[0] == 0 || pwm[1] == 0) {
			t.Fatalf("%s expected creep pwm, got %v", c.name, pwm)
		}
	}
}

func TestStopIsZero(t *testing.T) {
	m := PWM(Stop, true)
	if m != [4]int16{} {
		t.Fatalf("%v", m)
	}
}

func abs(v int16) int16 {
	if v < 0 {
		return -v
	}
	return v
}
