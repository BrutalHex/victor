package audio

import "testing"

func TestMixMono(t *testing.T) {
	in := []int16{100, 200, 300, 400, 10, 20, 30, 40}
	got := MixMono(in)
	if len(got) != 2 {
		t.Fatalf("len %d", len(got))
	}
	// Loudest channel is index 3 (400, 40).
	if got[0] != 400 || got[1] != 40 {
		t.Fatalf("%v", got)
	}
}

func TestPacketizer20ms(t *testing.T) {
	var p Packetizer
	samples := make([]int16, PacketSamp+40)
	pkts := p.Push(samples)
	if len(pkts) != 1 || len(pkts[0]) != PacketSamp*2 {
		t.Fatalf("pkts %d size %d", len(pkts), len(pkts[0]))
	}
	pkts = p.Push(make([]int16, PacketSamp-40))
	if len(pkts) != 1 {
		t.Fatalf("second %d", len(pkts))
	}
}

func TestPackRoundTrip(t *testing.T) {
	s := []int16{-1, 0, 1, 32767}
	got := UnpackPCM(PackPCM(s))
	for i := range s {
		if got[i] != s[i] {
			t.Fatalf("%v != %v", got, s)
		}
	}
}
