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
