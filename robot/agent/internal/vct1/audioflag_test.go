package vct1

import "testing"

func TestAudioRobotNoiseFlag(t *testing.T) {
	for _, noisy := range []bool{false, true} {
		h, payload, err := Decode(EncodeAudioFlags(7, []byte{1, 2, 3, 4}, noisy))
		if err != nil || len(payload) != 4 || h.Type != TypeAudio {
			t.Fatal(err)
		}
		if h.Flags&AudioRate16k == 0 || (h.Flags&FlagRobotNoise != 0) != noisy {
			t.Fatalf("flags %08b noisy=%v", h.Flags, noisy)
		}
	}
	if FlagRobotNoise&AudioRate16k != 0 {
		t.Fatal("bit clash")
	}
}
