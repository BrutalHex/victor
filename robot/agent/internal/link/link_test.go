package link

import (
	"encoding/binary"
	"testing"

	"github.com/BrutalHex/victor/robot/agent/internal/skill"
)

func TestRoundTrip(t *testing.T) {
	m := Msg{Type: 2, Seq: 9, Tns: 11, Kind: skill.CreepForward}
	got, err := Decode(Encode(m))
	if err != nil {
		t.Fatal(err)
	}
	if got.Kind != skill.CreepForward || got.Seq != 9 {
		t.Fatalf("%+v", got)
	}
}

func TestEncodeCmd(t *testing.T) {
	b := EncodeCmd(3, 4, CmdFaceUI, []byte("thinking|hi"))
	if string(b[:4]) != Magic || b[4] != TypeCmd || b[17] != CmdFaceUI {
		t.Fatalf("hdr %x", b[:18])
	}
	n := binary.LittleEndian.Uint32(b[18:22])
	if int(n) != 11 || string(b[22:]) != "thinking|hi" {
		t.Fatalf("payload n=%d %q", n, b[22:])
	}
}

func TestEncodeMedia(t *testing.T) {
	b := EncodeMedia(1, 2, []byte("VCT1xxxx"))
	if b[4] != TypeMedia {
		t.Fatal("type")
	}
	n := binary.LittleEndian.Uint32(b[18:22])
	if int(n) != 8 {
		t.Fatalf("n %d", n)
	}
}
