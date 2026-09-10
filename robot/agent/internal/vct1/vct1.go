// Package vct1 implements the GROK_INSTRUCTIONS.md UDP datagram framing.
package vct1

import (
	"encoding/binary"
	"errors"
	"hash/crc32"
	"time"
)

const (
	Magic      = "VCT1"
	TypeSensor = 1
	TypeAudio  = 2
	TypeVideo  = 3
	TypeUIAck  = 4
	HeaderSize = 4 + 1 + 1 + 4 + 8 + 4 // magic, type, flags, seq, t_ns, len
	CRCSize    = 4
)

var crcTable = crc32.MakeTable(crc32.IEEE)

type Header struct {
	Type  uint8
	Flags uint8
	Seq   uint32
	Tns   uint64
}

func Encode(h Header, payload []byte) []byte {
	buf := make([]byte, HeaderSize+len(payload)+CRCSize)
	copy(buf[0:4], Magic)
	buf[4] = h.Type
	buf[5] = h.Flags
	binary.LittleEndian.PutUint32(buf[6:10], h.Seq)
	binary.LittleEndian.PutUint64(buf[10:18], h.Tns)
	binary.LittleEndian.PutUint32(buf[18:22], uint32(len(payload)))
	copy(buf[22:], payload)
	crc := crc32.Checksum(buf[:22+len(payload)], crcTable)
	binary.LittleEndian.PutUint32(buf[22+len(payload):], crc)
	return buf
}

func Decode(buf []byte) (Header, []byte, error) {
	if len(buf) < HeaderSize+CRCSize {
		return Header{}, nil, errors.New("short datagram")
	}
	if string(buf[0:4]) != Magic {
		return Header{}, nil, errors.New("bad magic")
	}
	plen := binary.LittleEndian.Uint32(buf[18:22])
	need := HeaderSize + int(plen) + CRCSize
	if len(buf) < need {
		return Header{}, nil, errors.New("truncated payload")
	}
	got := binary.LittleEndian.Uint32(buf[HeaderSize+int(plen):])
	want := crc32.Checksum(buf[:HeaderSize+int(plen)], crcTable)
	if got != want {
		return Header{}, nil, errors.New("crc mismatch")
	}
	h := Header{
		Type:  buf[4],
		Flags: buf[5],
		Seq:   binary.LittleEndian.Uint32(buf[6:10]),
		Tns:   binary.LittleEndian.Uint64(buf[10:18]),
	}
	payload := make([]byte, plen)
	copy(payload, buf[HeaderSize:HeaderSize+int(plen)])
	return h, payload, nil
}

func NowTns() uint64 {
	return uint64(time.Now().UnixNano())
}

// Sensor is the packed SENSOR payload (≤64 bytes).
type Sensor struct {
	Cliffs      [4]uint16
	ProxMM      uint16
	ProxQuality uint8
	IMUA        [3]int16
	IMUG        [3]int16
	EncRW       int32
	EncLW       int32
	EncLift     int32
	EncHead     int32
	BattMV      uint16
	ChargerMV   uint16
	Touch       uint16
	Flags       uint16 // bit0 button, bit1 picked_up, bit2 on_charger, bit3 falling
}

func (s Sensor) Marshal() []byte {
	b := make([]byte, 47)
	off := 0
	for i := 0; i < 4; i++ {
		binary.LittleEndian.PutUint16(b[off:], s.Cliffs[i])
		off += 2
	}
	binary.LittleEndian.PutUint16(b[off:], s.ProxMM)
	off += 2
	b[off] = s.ProxQuality
	off++
	for i := 0; i < 3; i++ {
		binary.LittleEndian.PutUint16(b[off:], uint16(s.IMUA[i]))
		off += 2
	}
	for i := 0; i < 3; i++ {
		binary.LittleEndian.PutUint16(b[off:], uint16(s.IMUG[i]))
		off += 2
	}
	binary.LittleEndian.PutUint32(b[off:], uint32(s.EncRW))
	off += 4
	binary.LittleEndian.PutUint32(b[off:], uint32(s.EncLW))
	off += 4
	binary.LittleEndian.PutUint32(b[off:], uint32(s.EncLift))
	off += 4
	binary.LittleEndian.PutUint32(b[off:], uint32(s.EncHead))
	off += 4
	binary.LittleEndian.PutUint16(b[off:], s.BattMV)
	off += 2
	binary.LittleEndian.PutUint16(b[off:], s.ChargerMV)
	off += 2
	binary.LittleEndian.PutUint16(b[off:], s.Touch)
	off += 2
	binary.LittleEndian.PutUint16(b[off:], s.Flags)
	return b
}

func UnmarshalSensor(b []byte) (Sensor, error) {
	var s Sensor
	if len(b) < 47 {
		return s, errors.New("short sensor")
	}
	off := 0
	for i := 0; i < 4; i++ {
		s.Cliffs[i] = binary.LittleEndian.Uint16(b[off:])
		off += 2
	}
	s.ProxMM = binary.LittleEndian.Uint16(b[off:])
	off += 2
	s.ProxQuality = b[off]
	off++
	for i := 0; i < 3; i++ {
		s.IMUA[i] = int16(binary.LittleEndian.Uint16(b[off:]))
		off += 2
	}
	for i := 0; i < 3; i++ {
		s.IMUG[i] = int16(binary.LittleEndian.Uint16(b[off:]))
		off += 2
	}
	s.EncRW = int32(binary.LittleEndian.Uint32(b[off:]))
	off += 4
	s.EncLW = int32(binary.LittleEndian.Uint32(b[off:]))
	off += 4
	s.EncLift = int32(binary.LittleEndian.Uint32(b[off:]))
	off += 4
	s.EncHead = int32(binary.LittleEndian.Uint32(b[off:]))
	off += 4
	s.BattMV = binary.LittleEndian.Uint16(b[off:])
	off += 2
	s.ChargerMV = binary.LittleEndian.Uint16(b[off:])
	off += 2
	s.Touch = binary.LittleEndian.Uint16(b[off:])
	off += 2
	s.Flags = binary.LittleEndian.Uint16(b[off:])
	return s, nil
}

const (
	FlagButton    uint16 = 1 << 0
	FlagPickedUp  uint16 = 1 << 1
	FlagOnCharger uint16 = 1 << 2
	FlagFalling   uint16 = 1 << 3
)
