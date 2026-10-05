package audio

import "testing"

func TestMixMonoKeepsQuietest(t *testing.T) {
	in := []int16{100, 200, 300, 400, 10, 20, 30, 40}
	got := MixMono(in)
	if len(got) != 2 {
		t.Fatalf("len %d", len(got))
	}
	// Channel 0 is quietest (100, 10). The loud channels are grind.
	if got[0] != 100 || got[1] != 10 {
		t.Fatalf("%v", got)
	}
	e := Energies(in)
	if e[3] <= e[2] {
		t.Fatalf("energies %v", e)
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
