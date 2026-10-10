package vct1

import "testing"

func TestRoundTrip(t *testing.T) {
	s := Sensor{
		Cliffs: [4]uint16{1, 2, 3, 4},
		ProxMM: 120, ProxQuality: 9,
		EncLift: 700, BattMV: 3900, ChargerMV: 5000, Touch: 11,
		Flags: FlagOnCharger | FlagButton, ButtonPresses: 3, ActionSeq: 5, ActionResult: ResultRearCliff,
	}
	payload := s.Marshal()
	if len(payload) != SensorSize || SensorSize != 52 {
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

func TestAudioVideo(t *testing.T) {
	pcm := make([]byte, 640)
	buf := EncodeAudio(3, pcm)
	h, p, err := Decode(buf)
	if err != nil || h.Type != TypeAudio || len(p) != 640 {
		t.Fatalf("%+v %d %v", h, len(p), err)
	}
	jpeg := []byte{0xff, 0xd8, 0xff, 0xd9}
	buf = EncodeVideo(4, jpeg, FlagFaceJPEG)
	h, p, err = Decode(buf)
	if err != nil || h.Type != TypeVideo || h.Flags != FlagFaceJPEG || string(p) != string(jpeg) {
		t.Fatalf("%+v %v", h, err)
	}
}

func TestOldSensorWithoutPressCounter(t *testing.T) {
	s := Sensor{BattMV: 3900, Flags: FlagOnCharger, ButtonPresses: 9}
	got, err := UnmarshalSensor(s.Marshal()[:47])
	if err != nil || got.ButtonPresses != 0 || got.BattMV != 3900 || got.Flags != FlagOnCharger {
		t.Fatalf("%+v %v", got, err)
	}
}
