// Package veto is the on-robot motor interlock. It runs even if the hub is dead.
package veto

import "time"

const (
	Heartbeat = 250 * time.Millisecond
	CommandTTL = 200 * time.Millisecond
	BattStopMV = 3450
	CliffRatio = 0.40
	ToFMaxMM   = 400
)

type Reason uint8

const (
	Clear Reason = iota
	Cliff
	ToF
	Pickup
	Fall
	HeartbeatMiss
	TTL
	Battery
)

func (r Reason) String() string {
	switch r {
	case Clear:
		return "clear"
	case Cliff:
		return "cliff"
	case ToF:
		return "tof"
	case Pickup:
		return "pickup"
	case Fall:
		return "fall"
	case HeartbeatMiss:
		return "heartbeat"
	case TTL:
		return "ttl"
	case Battery:
		return "battery"
	default:
		return "unknown"
	}
}

type Input struct {
	Cliffs       [4]uint16
	Thresh       [4]uint16 // 0 = that channel not calibrated
	Calibrated   bool
	ProxMM       uint16
	Forward      bool
	OnCharger    bool
	PickedUp     bool
	Falling      bool
	HeartbeatAge time.Duration
	HasHeartbeat bool
	CommandAge   time.Duration
	HasCommand   bool
	BattMV       uint16
}

func Check(in Input) Reason {
	if in.BattMV > 0 && in.BattMV < BattStopMV {
		return Battery
	}
	if in.Falling {
		return Fall
	}
	if in.PickedUp || allCliffsVoid(in) {
		return Pickup
	}
	if frontCliff(in) {
		return Cliff
	}
	// Close return while creeping: obstacle. 0 = no return (open), not a stop.
	if in.Forward && in.ProxMM > 0 && in.ProxMM < 80 {
		return ToF
	}
	if in.HasHeartbeat && in.HeartbeatAge > Heartbeat {
		return HeartbeatMiss
	}
	if !in.HasHeartbeat {
		return HeartbeatMiss
	}
	if in.HasCommand && in.CommandAge > CommandTTL {
		return TTL
	}
	return Clear
}

func frontCliff(in Input) bool {
	for i := 0; i < 2; i++ {
		if in.Cliffs[i] < 40 {
			return true
		}
		if in.Calibrated && in.Thresh[i] > 0 && in.Cliffs[i] < in.Thresh[i] {
			return true
		}
	}
	return false
}

func allCliffsVoid(in Input) bool {
	if in.OnCharger {
		return false
	}
	n := 0
	for i := 0; i < 4; i++ {
		th := in.Thresh[i]
		if th == 0 {
			if in.Cliffs[i] < 40 {
				n++
			}
			continue
		}
		if in.Cliffs[i] < th {
			n++
		}
	}
	return n == 4
}
