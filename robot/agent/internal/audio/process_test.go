package audio

import (
	"math"
	"testing"
)

func TestResample15625To16000(t *testing.T) {
	var r resampler
	in := make([]float64, 1250)
	for i := range in {
		in[i] = 1000
	}
	out := r.push(in)
	// 125 in -> 128 out, minus one sample held for interpolation.
	if len(out) < 1270 || len(out) > 1280 {
		t.Fatalf("len %d", len(out))
	}
	if math.Abs(out[len(out)/2]-1000) > 1 {
		t.Fatalf("level %f", out[len(out)/2])
	}
}

func TestHighpassKillsDC(t *testing.T) {
	b := newHighpass(hpHz, RawRate)
	var last float64
	for i := 0; i < 2000; i++ {
		last = b.step(10000)
	}
	if math.Abs(last) > 50 {
		t.Fatalf("dc leaked %f", last)
	}
	tone := 0.0
	for i := 0; i < 800; i++ {
		tone = b.step(8000 * math.Sin(2*math.Pi*1000*float64(i)/RawRate))
	}
	if math.Abs(tone) < 1000 {
		t.Fatalf("speech band attenuated %f", tone)
	}
}

func TestDirectionalPicksFront(t *testing.T) {
	p := NewProcessor(ModeDirectional)
	n := 400
	raw := make([]int16, n*Channels)
	for i := 0; i < n; i++ {
		s := 8000 * math.Sin(2*math.Pi*900*float64(i)/RawRate)
		rear := 8000 * math.Sin(2*math.Pi*900*float64(i-3)/RawRate)
		// order rl, fl, rr, fr. Front leads rear by one sample.
		raw[i*Channels+0] = int16(rear)
		raw[i*Channels+2] = int16(rear)
		raw[i*Channels+1] = int16(s)
		raw[i*Channels+3] = int16(s)
	}
	out := p.Process(raw)
	if len(out) < 200 {
		t.Fatalf("short %d", len(out))
	}
	if p.Direction() != 0 && p.Direction() != 11 && p.Direction() != 1 {
		t.Fatalf("dir %d, want forward", p.Direction())
	}
}

func TestGrindChannelDropped(t *testing.T) {
	p := NewProcessor(ModeDirectional)
	n := 600
	raw := make([]int16, n*Channels)
	for i := 0; i < n; i++ {
		tone := int16(2500 * math.Sin(2*math.Pi*700*float64(i)/RawRate))
		grind := int16(20000 * math.Sin(2*math.Pi*120*float64(i)/RawRate))
		raw[i*Channels+0] = tone
		raw[i*Channels+1] = tone
		raw[i*Channels+2] = grind
		raw[i*Channels+3] = tone
	}
	// Prime the noise tracker on grind-only, then the tone.
	prime := make([]int16, 200*Channels)
	for i := 0; i < 200; i++ {
		g := int16(20000 * math.Sin(2*math.Pi*120*float64(i)/RawRate))
		prime[i*Channels+2] = g
	}
	_ = p.Process(prime)
	out := p.Process(raw)
	if len(out) < 100 {
		t.Fatalf("short %d", len(out))
	}
	var tone, grind float64
	for i, s := range out {
		th := math.Sin(2 * math.Pi * 700 * float64(i) / Rate)
		gh := math.Sin(2 * math.Pi * 120 * float64(i) / Rate)
		tone += float64(s) * th
		grind += float64(s) * gh
	}
	if math.Abs(tone) < math.Abs(grind) {
		t.Fatalf("grind won tone=%f grind=%f", tone, grind)
	}
}

func TestEchoReduced(t *testing.T) {
	p := NewProcessor(ModeFast)
	n := 1600
	ref := make([]int16, n)
	mic := make([]int16, n*Channels)
	for i := 0; i < n; i++ {
		ref[i] = int16(6000 * math.Sin(2*math.Pi*300*float64(i)/Rate))
	}
	p.NotePlayback(PackPCM(ref))
	for i := 0; i < n; i++ {
		j := i - 40
		var echo int16
		if j >= 0 {
			echo = ref[j] / 2
		}
		mic[i*Channels] = echo
	}
	out := p.Process(mic)
	if len(out) < 400 {
		t.Fatalf("short %d", len(out))
	}
	var before, after float64
	for i := 200; i < 400 && i < len(out); i++ {
		before += float64(ref[i]) * float64(ref[i])
		after += float64(out[i]) * float64(out[i])
	}
	if after > before*0.5 {
		t.Fatalf("echo not reduced after=%f before=%f", after, before)
	}
}

func TestVoiceDetectKeepsAllQuiet(t *testing.T) {
	p := NewProcessor(ModeVoiceDetect)
	n := 320
	raw := make([]int16, n*Channels)
	for i := 0; i < n; i++ {
		s := int16(4000 * math.Sin(2*math.Pi*500*float64(i)/RawRate))
		raw[i*Channels+0] = s
		raw[i*Channels+1] = s
		raw[i*Channels+3] = s
		raw[i*Channels+2] = 20000
	}
	out := p.Process(raw)
	if len(out) == 0 {
		t.Fatal("empty")
	}
}
