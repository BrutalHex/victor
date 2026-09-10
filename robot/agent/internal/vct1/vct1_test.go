package vct1

import "testing"

func TestRoundTrip(t *testing.T) {
	s := Sensor{
		Cliffs: [4]uint16{1, 2, 3, 4},
		ProxMM: 120, ProxQuality: 9,
		EncLift: 700, BattMV: 3900, ChargerMV: 5000, Touch: 11,
		Flags: FlagOnCharger | FlagButton,
	}
	payload := s.Marshal()
	if len(payload) != 47 {
		t.Fatalf("payload %d", len(payload))
	}
	buf := Encode(Header{Type: TypeSensor, Seq: 7, Tns: 99}, payload)
	h, p, err := Decode(buf)
	if err != nil {
		t.Fatal(err)
	}
	if h.Type != TypeSensor || h.Seq != 7 || h.Tns != 99 {
		t.Fatalf("header %+v", h)
	}
	got, err := UnmarshalSensor(p)
	if err != nil {
		t.Fatal(err)
	}
	if got != s {
		t.Fatalf("sensor %+v != %+v", got, s)
	}
}

func TestCRCMismatch(t *testing.T) {
	buf := Encode(Header{Type: 1}, []byte{1, 2, 3})
	buf[len(buf)-1] ^= 0xff
	if _, _, err := Decode(buf); err == nil {
		t.Fatal("expected crc error")
	}
}
