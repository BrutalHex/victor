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
	// Touch is the backpack capacitive level the stock engine uses
	// (backpackTouchSensorRaw): touchHires[0] on production bodies, else
	// touchLevel[0]^1.2. TouchLevel is touchLevel[0] as sent.
	Touch      uint16
	TouchLevel uint16
	TouchHires uint16
	Button     bool
	// Mic is 4 channels × 80 samples (interleaved) when the 768-byte dataframe is present.
	Mic []int16
}

const (
	packedMin = 95
	// audio[] follows touchLevel, micError, touchHires and _unused[24] in the
	// packed BodyToHead (robot/syscon/schema/messages.h): 4+1+1+2 header,
	// 4×12 motors, 4×2 cliffs, 12 battery, 16 range, 3×4 touch/mic/hires,
	// 24 spare = 128, and 128 + 640 = the 768-byte dataframe. 125 put the
	// low byte of each sample in the high byte of the next: a quiet room read
	// as ±1000 hiss and any voice wrapped into full-scale white noise.
	micOffset  = 128
	micSamples = 320
	micBytes   = micSamples * 2
)

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
	// Button: unchanged on purpose (it feeds CHARGE-LATCH). See ButtonBytes.
	f.Button = binary.LittleEndian.Uint16(b[touchOff+2:]) > 0
	f.TouchLevel, f.TouchHires, f.Touch = touchFields(b)
	if len(b) >= micOffset+micBytes {
		f.Mic = make([]int16, micSamples)
		for i := 0; i < micSamples; i++ {
			f.Mic[i] = int16(binary.LittleEndian.Uint16(b[micOffset+i*2:]))
		}
	}
	return f, nil
}

// Real BodyToHead offsets (robot/syscon/schema/messages.h, checked against
// recorded frames): battery is 12 bytes (64..75), RangeData 16 (76..91),
// touchLevel[2] at 92, micError[2] at 96, touchHires[2] at 100. The old
// touch read at 89 (inside RangeData) and was always 0 on hardware.
const (
	touchLevelOff = 92
	touchHiresOff = 100
)

// ButtonBytes is where Button is read from (offset 91, 2 bytes). Kept as
// is for the latch; note it overlaps calibrationResult's top byte and the
// low byte of touchLevel[0] (see deploy notes / tests).
const ButtonBytes = 91

func touchFields(b []byte) (level, hires, touch uint16) {
	if len(b) >= touchHiresOff+2 {
		level = binary.LittleEndian.Uint16(b[touchLevelOff:])
		hires = binary.LittleEndian.Uint16(b[touchHiresOff:])
	}
	switch {
	case hires != 0 && hires != 0xFFFF:
		touch = hires
	case level != 0 && level != 0xFFFF:
		touch = uint16(math.Min(65534, math.Pow(float64(level), 1.2)))
	}
	return level, hires, touch
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
