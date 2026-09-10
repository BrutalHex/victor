package spine

import (
	"encoding/binary"
	"errors"
)

const (
	HeaderTX     = 0x423248AA // head → body
	HeaderRX     = 0x483242AA // body → head
	TypeData     = 0x6466     // 'df'
	TypeVersion  = 0x7276     // 'vr'
	TypeMode     = 0x6D64     // 'md'
	TypeLED      = 0x736C     // 'ls'
	TypeShutdown = 0x6473     // 'sd'
	CtrlSize     = 64
	DataRXSize   = 768
)

func Encode(frameType uint16, payload []byte) []byte {
	n := len(payload)
	buf := make([]byte, 8+n+4)
	binary.LittleEndian.PutUint32(buf[0:4], HeaderTX)
	binary.LittleEndian.PutUint16(buf[4:6], frameType)
	binary.LittleEndian.PutUint16(buf[6:8], uint16(n))
	copy(buf[8:], payload)
	binary.LittleEndian.PutUint32(buf[8+n:], ankiCRC(payload))
	return buf
}

func EncodeCtrl(seq uint32, motors [4]int16, leds [12]byte) []byte {
	var ctrl [CtrlSize]byte
	binary.LittleEndian.PutUint32(ctrl[0:4], seq)
	binary.LittleEndian.PutUint32(ctrl[4:8], 0)
	off := 8
	for i := 0; i < 4; i++ {
		binary.LittleEndian.PutUint16(ctrl[off:], uint16(motors[i]))
		off += 2
	}
	copy(ctrl[off:], leds[:])
	return Encode(TypeData, ctrl[:])
}

type WireFrame struct {
	Type    uint16
	Payload []byte
}

func Split(buf []byte) (rest []byte, frames []WireFrame) {
	for {
		start := -1
		if len(buf) < 8 {
			return buf, frames
		}
		for i := 0; i+4 <= len(buf); i++ {
			if binary.LittleEndian.Uint32(buf[i:]) == HeaderRX {
				start = i
				break
			}
		}
		if start < 0 {
			if len(buf) > 4 {
				buf = buf[len(buf)-4:]
			}
			return buf, frames
		}
		if start > 0 {
			buf = buf[start:]
		}
		if len(buf) < 8 {
			return buf, frames
		}
		typ := binary.LittleEndian.Uint16(buf[4:6])
		size := int(binary.LittleEndian.Uint16(buf[6:8]))
		need := 8 + size + 4
		if size < 0 || size > 2048 {
			buf = buf[4:]
			continue
		}
		if len(buf) < need {
			return buf, frames
		}
		payload := buf[8 : 8+size]
		got := binary.LittleEndian.Uint32(buf[8+size:])
		if ankiCRC(payload) != got {
			buf = buf[4:]
			continue
		}
		p := make([]byte, size)
		copy(p, payload)
		frames = append(frames, WireFrame{Type: typ, Payload: p})
		buf = buf[need:]
	}
}

func ParseData(payload []byte) (Frame, error) {
	if len(payload) < packedMin {
		return Frame{}, errors.New("short dataframe")
	}
	return ParsePacked(payload)
}
