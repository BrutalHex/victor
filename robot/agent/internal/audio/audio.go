// Package audio mixes spine mics to 16 kHz mono and plays hub TTS on the speaker.
package audio

import (
	"bytes"
	"encoding/binary"
	"os"
	"os/exec"
)

const (
	Rate       = 16000
	PacketSamp = 320 // 20 ms at 16 kHz
	Channels   = 4
	PerChan    = 80
	SpeakFile  = "/data/victor/speak.pcm"
	MicFile    = "/data/victor/mic.pcm"
)

// MixMono picks the loudest of 4 interleaved mic channels (Vector backpack array).
func MixMono(interleaved []int16) []int16 {
	if len(interleaved) < Channels {
		return nil
	}
	n := len(interleaved) / Channels
	best := 0
	bestE := int64(-1)
	for c := 0; c < Channels; c++ {
		var e int64
		for i := 0; i < n; i++ {
			v := int64(interleaved[i*Channels+c])
			e += v * v
		}
		if e > bestE {
			bestE = e
			best = c
		}
	}
	out := make([]int16, n)
	for i := 0; i < n; i++ {
		out[i] = interleaved[i*Channels+best]
	}
	return out
}

// PackPCM writes s16le bytes.
func PackPCM(samples []int16) []byte {
	if len(samples) == 0 {
		return nil
	}
	b := make([]byte, len(samples)*2)
	for i, s := range samples {
		binary.LittleEndian.PutUint16(b[i*2:], uint16(s))
	}
	return b
}

func UnpackPCM(b []byte) []int16 {
	n := len(b) / 2
	out := make([]int16, n)
	for i := 0; i < n; i++ {
		out[i] = int16(binary.LittleEndian.Uint16(b[i*2:]))
	}
	return out
}

// InjectMic reads /data/victor/mic.pcm if present (prove / tests).
func InjectMic() []int16 {
	b, err := os.ReadFile(MicFile)
	if err != nil || len(b) < 2 {
		return nil
	}
	_ = os.Remove(MicFile)
	return UnpackPCM(b)
}

type Packetizer struct {
	buf []int16
}

func (p *Packetizer) Push(samples []int16) [][]byte {
	if len(samples) == 0 {
		return nil
	}
	p.buf = append(p.buf, samples...)
	var out [][]byte
	for len(p.buf) >= PacketSamp {
		out = append(out, PackPCM(p.buf[:PacketSamp]))
		p.buf = p.buf[PacketSamp:]
	}
	return out
}

// Play writes PCM for tests and tries ALSA aplay. Speaker is on the head SoC.
func Play(pcm []byte) error {
	if len(pcm) == 0 {
		return nil
	}
	_ = os.MkdirAll("/data/victor", 0755)
	_ = os.WriteFile(SpeakFile, pcm, 0644)
	if _, err := exec.LookPath("aplay"); err != nil {
		return nil
	}
	cmd := exec.Command("aplay", "-q", "-r", "16000", "-f", "S16_LE", "-c", "1", "-")
	cmd.Stdin = bytes.NewReader(pcm)
	_ = cmd.Run()
	return nil
}
