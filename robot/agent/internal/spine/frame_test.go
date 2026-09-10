package spine

import (
	"encoding/binary"
	"testing"
)

func TestEncodeRoundTripCRC(t *testing.T) {
	payload := []byte{1, 2, 3, 4}
	buf := Encode(TypeVersion, payload)
	if binary.LittleEndian.Uint32(buf[0:4]) != HeaderTX {
		t.Fatal("tx header")
	}
	// RX parser looks for HeaderRX; swap for the test by rewriting.
	binary.LittleEndian.PutUint32(buf[0:4], HeaderRX)
	rest, frames := Split(buf)
	if len(rest) != 0 || len(frames) != 1 {
		t.Fatalf("split rest=%d frames=%d", len(rest), len(frames))
	}
	if frames[0].Type != TypeVersion {
		t.Fatalf("type %x", frames[0].Type)
	}
	if string(frames[0].Payload) != string(payload) {
		t.Fatalf("payload %x", frames[0].Payload)
	}
}

func TestParsePackedFields(t *testing.T) {
	b := make([]byte, 768)
	binary.LittleEndian.PutUint32(b[0:4], 42)
	binary.LittleEndian.PutUint16(b[4:6], 7)
	// motors start at 8; lift is index 2
	binary.LittleEndian.PutUint32(b[8+24:], uint32(900))
	binary.LittleEndian.PutUint16(b[56:], 111)
	binary.LittleEndian.PutUint16(b[64:], 2000) // batt
	binary.LittleEndian.PutUint16(b[66:], 3000) // charger
	binary.LittleEndian.PutUint16(b[91:], 1)    // button
	f, err := ParsePacked(b)
	if err != nil {
		t.Fatal(err)
	}
	if f.Seq != 42 || f.Motors[2].Pos != 900 || f.Cliffs[0] != 111 {
		t.Fatalf("%+v", f)
	}
	if !f.Button || !f.OnCharger() {
		t.Fatalf("button/charger %+v", f)
	}
	s := f.Sensor()
	if s.EncLift != 900 || s.Flags&1 == 0 {
		t.Fatalf("sensor %+v", s)
	}
}

func TestBadCRCDropped(t *testing.T) {
	buf := Encode(TypeData, make([]byte, 8))
	binary.LittleEndian.PutUint32(buf[0:4], HeaderRX)
	buf[len(buf)-1] ^= 0xff
	_, frames := Split(buf)
	if len(frames) != 0 {
		t.Fatal("expected drop")
	}
}
