// Package skill is the only locomotion language the hub may speak.
// PWM stays on the robot. ChatGPT never reaches this package.
package skill

import "time"

type Kind uint8

const (
	Idle Kind = iota
	Stop
	BackOff
	TurnLeft
	TurnRight
	CreepForward
	LookUp
	LookDown
	Dock
)

func Parse(s string) Kind {
	switch s {
	case "stop":
		return Stop
	case "back_off":
		return BackOff
	case "turn_left":
		return TurnLeft
	case "turn_right":
		return TurnRight
	case "creep_forward":
		return CreepForward
	case "look_up":
		return LookUp
	case "look_down":
		return LookDown
	case "dock":
		return Dock
	default:
		return Idle
	}
}

func (k Kind) String() string {
	switch k {
	case Stop:
		return "stop"
	case BackOff:
		return "back_off"
	case TurnLeft:
		return "turn_left"
	case TurnRight:
		return "turn_right"
	case CreepForward:
		return "creep_forward"
	case LookUp:
		return "look_up"
	case LookDown:
		return "look_down"
	case Dock:
		return "dock"
	default:
		return "idle"
	}
}

func (k Kind) Forward() bool {
	return k == CreepForward
}

type Command struct {
	Kind   Kind
	Issued time.Time
	TTL    time.Duration
}

func (c Command) Expired(now time.Time) bool {
	if c.TTL == 0 {
		c.TTL = 200 * time.Millisecond
	}
	return now.Sub(c.Issued) > c.TTL
}

// Twist is mm/s and rad/s. Cap is 50 mm/s until proven on blocks.
func (k Kind) Twist() (vx, wz float64) {
	const creep = 50.0
	switch k {
	case CreepForward:
		return creep, 0
	case BackOff:
		return -40, 0
	case TurnLeft:
		return 0, 0.4
	case TurnRight:
		return 0, -0.4
	default:
		return 0, 0
	}
}

func (k Kind) Head() int16 {
	switch k {
	case LookUp:
		return 800
	case LookDown:
		return -800
	default:
		return 0
	}
}
