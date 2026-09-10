// Package spine parses Body-to-Head frames. Layout is the packed
// spine_dataframe_t used by the Vector syscon (hardware reference only).
package spine

import (
	"encoding/binary"
	"errors"
	"math"
)

type Motor struct {
	Pos int32
	Dlt int32
	Tm  uint32
}

type Frame struct {
	Seq            uint32
	Status         uint16
	Cliffs         [4]uint16
	Motors         [4]Motor // right wheel, left wheel, lift, head
	BattVoltage    int16
	ChargerVoltage int16
	BodyTemp       int16
	BatteryFlags   uint16
	ProxSigmaMM    uint8
	ProxRawRangeMM uint16
	Touch          uint16
	Button         bool
}

const packedMin = 95

func ParsePacked(b []byte) (Frame, error) {
	var f Frame
	if len(b) < packedMin {
		return f, errors.New("short spine payload")
	}
	f.Seq = binary.LittleEndian.Uint32(b[0:4])
	f.Status = binary.LittleEndian.Uint16(b[4:6])
	off := 8
	for i := 0; i < 4; i++ {
		f.Motors[i].Pos = int32(binary.LittleEndian.Uint32(b[off:]))
		f.Motors[i].Dlt = int32(binary.LittleEndian.Uint32(b[off+4:]))
		f.Motors[i].Tm = binary.LittleEndian.Uint32(b[off+8:])
		off += 12
	}
	for i := 0; i < 4; i++ {
		f.Cliffs[i] = binary.LittleEndian.Uint16(b[off:])
		off += 2
	}
	f.BattVoltage = int16(binary.LittleEndian.Uint16(b[off:]))
	f.ChargerVoltage = int16(binary.LittleEndian.Uint16(b[off+2:]))
	f.BodyTemp = int16(binary.LittleEndian.Uint16(b[off+4:]))
	f.BatteryFlags = binary.LittleEndian.Uint16(b[off+6:])
	off += 10 // batt, charger, temp, flags, reserved1
	f.ProxSigmaMM = b[off]
	f.ProxRawRangeMM = binary.LittleEndian.Uint16(b[off+1:])
	// packed: sigma u8, raw u16 at +1; skip remaining prox + touch/button
	// after reserved1 (2 bytes already consumed in the +10):
	// prox_sigma (1) + prox fields (2*5+4=14) = 15, then touch u16, button u16
	touchOff := off + 1 + 14
	if len(b) < touchOff+4 {
		return f, errors.New("short spine tail")
	}
	f.Touch = binary.LittleEndian.Uint16(b[touchOff:])
	f.Button = binary.LittleEndian.Uint16(b[touchOff+2:]) > 0
	return f, nil
}

func (f Frame) OnCharger() bool {
	if f.BatteryFlags&0x1 != 0 {
		return true
	}
	return f.ChargerVoltage > 1000
}

func (f Frame) Driving() bool {
	return abs32(f.Motors[0].Dlt) > 40 || abs32(f.Motors[1].Dlt) > 40
}

func (f Frame) LiftNorm(minPos, maxPos int32) float64 {
	if maxPos <= minPos {
		return 0
	}
	n := float64(f.Motors[2].Pos-minPos) / float64(maxPos-minPos)
	return math.Max(0, math.Min(1, n))
}

func abs32(v int32) int32 {
	if v < 0 {
		return -v
	}
	return v
}
