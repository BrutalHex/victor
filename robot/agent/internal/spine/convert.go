package spine

import "github.com/BrutalHex/victor/robot/agent/internal/vct1"

// millivolts applies the body-board scale (≈1.367 mV per count).
func millivolts(raw int16) uint16 {
	if raw <= 0 {
		return 0
	}
	v := int(float64(raw) * 1.36719)
	if v > 65535 {
		return 65535
	}
	return uint16(v)
}

func (f Frame) Sensor() vct1.Sensor {
	var s vct1.Sensor
	for i := 0; i < 4; i++ {
		s.Cliffs[i] = f.Cliffs[i]
	}
	s.ProxMM = f.ProxRawRangeMM
	s.ProxQuality = f.ProxSigmaMM
	s.EncRW = f.Motors[0].Pos
	s.EncLW = f.Motors[1].Pos
	s.EncLift = f.Motors[2].Pos
	s.EncHead = f.Motors[3].Pos
	s.BattMV = millivolts(f.BattVoltage)
	s.ChargerMV = millivolts(f.ChargerVoltage)
	s.Touch = f.Touch
	if f.Button {
		s.Flags |= vct1.FlagButton
	}
	if f.OnCharger() {
		s.Flags |= vct1.FlagOnCharger
	}
	return s
}
