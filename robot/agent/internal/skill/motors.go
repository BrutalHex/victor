package skill

import "os"

// Wheel mapping from Vector syscon HAL (hardware reference):
// motors[0] right wheel, motors[1] left wheel, motors[2] lift, motors[3] head.
// The body expects the left wheel command inverted so +vx is forward.
// Scale is uncalibrated. 1200 is the Phase-2 creep cap (~40–60 mm/s class).
// Non-zero wheel PWM requires /data/victor/explore.enabled AND a clear veto.

const (
	ExploreFlag = "/data/victor/explore.enabled"
	creepPWM    = 1200
)

func ExploreEnabled() bool {
	_, err := os.Stat(ExploreFlag)
	return err == nil
}

func PWM(k Kind, allowWheels bool) [4]int16 {
	var m [4]int16
	if !allowWheels {
		m[3] = k.Head()
		return m
	}
	vx, wz := k.Twist()
	// Differential: right = vx - wz*base, left = -(vx + wz*base)
	right := vx - wz*80
	left := vx + wz*80
	m[0] = scale(right)
	m[1] = -scale(left)
	m[3] = k.Head()
	return m
}

func scale(mmps float64) int16 {
	if mmps == 0 {
		return 0
	}
	v := mmps / 50.0 * float64(creepPWM)
	if v > float64(creepPWM) {
		v = float64(creepPWM)
	}
	if v < -float64(creepPWM) {
		v = -float64(creepPWM)
	}
	return int16(v)
}
