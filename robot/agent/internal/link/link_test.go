package link

import (
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
