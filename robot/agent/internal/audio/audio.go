package audio

import (
	"bytes"
	"encoding/binary"
	"math"
	"math/rand"
	"os"
	"os/exec"
	"sync"
)

// playMu keeps one aplay at a time: speech waits for a short purr, a purr
// never interrupts speech (PlaySoft gives up if the speaker is busy).
var playMu sync.Mutex

const (
	Rate       = 16000
	PacketSamp = 320 // 20 ms at 16 kHz
	Channels   = 4
	PerChan    = 80
	SpeakFile  = "/data/victor/speak.pcm"
	MicFile    = "/data/victor/mic.pcm"
)

// Energies is the sum of squares for each interleaved backpack mic.
func Energies(interleaved []int16) [Channels]int64 {
	var e [Channels]int64
	if len(interleaved) < Channels {
		return e
	}
	n := len(interleaved) / Channels
	for c := 0; c < Channels; c++ {
		var acc int64
		for i := 0; i < n; i++ {
			v := int64(interleaved[i*Channels+c])
			acc += v * v
		}
		e[c] = acc
	}
	return e
}

// MixMono keeps the quietest backpack channel. It is the fallback when the
// processor is nil. The live path picks one speech mic in Processor (fast,
// the default). Averaging the grind channels (Vector-W1V9: ch0 ~3e10, ch2
// ~2e11) mixes the tracks into the voice.
func MixMono(interleaved []int16) []int16 {
	if len(interleaved) < Channels {
		return nil
	}
	n := len(interleaved) / Channels
	e := Energies(interleaved)
	keep := 0
	for c := 1; c < Channels; c++ {
		if e[c] < e[keep] {
			keep = c
		}
	}
	out := make([]int16, n)
	for i := 0; i < n; i++ {
		out[i] = interleaved[i*Channels+keep]
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
	playMu.Lock()
	defer playMu.Unlock()
	return play(pcm)
}

// PlaySoft plays only if the speaker is free (petting purr); false if busy.
func PlaySoft(pcm []byte) bool {
	if len(pcm) == 0 || !playMu.TryLock() {
		return false
	}
	defer playMu.Unlock()
	_ = play(pcm)
	return true
}

func play(pcm []byte) error {
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

// Purr is a soft, low purr (about -30 dBFS): noise bursts at ~24 Hz through
// a low-pass, with a slow swell. Stock Vector plays a petting sound from its
// Wwise banks, which are not on this robot.
func Purr(d float64) []byte {
	n := int(d * Rate)
	out := make([]byte, n*2)
	r := rand.New(rand.NewSource(42))
	lp := 0.0
	for i := 0; i < n; i++ {
		t := float64(i) / Rate
		burst := 0.5 + 0.5*math.Sin(2*math.Pi*24*t)
		burst *= burst
		swell := math.Sin(math.Pi * t / d)
		x := (r.Float64()*2 - 1) * burst
		lp += 0.06 * (x - lp)
		v := lp * swell * 2400
		binary.LittleEndian.PutUint16(out[i*2:], uint16(int16(v)))
	}
	return out
}
