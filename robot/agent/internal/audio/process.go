package audio

import (
	"math"
	"os"
	"strings"
	"sync"
)

// Stock vic-engine is gone once prove-phase1 owns the spine, so the three SDK
// modes are applied here to the raw backpack array.
//
//	Fast:        one unprocessed mic (quietest after the high-pass)
//	Directional: 12-look delay-and-sum, the clean path (default)
//	VoiceDetect: multi-mic, no beam, for voice activity
//
// Raw spine rate is 15625 Hz (SDK MICROPHONE_SAMPLE_RATE). Output is 16000 Hz
// mono, which is what the hub VAD and STT expect. No Apache-2 Go library does
// 16 kHz beamforming plus echo cancel without CGO, so this stays in-tree and
// cross-compiles onto the APQ8009.

const (
	RawRate    = 15625
	Directions = 12
	hpHz       = 150.0
	targetRMS  = 1800.0
)

// Mode is the SDK audio processing mode.
type Mode int

const (
	ModeFast Mode = iota
	ModeDirectional
	ModeVoiceDetect
)

func (m Mode) String() string {
	switch m {
	case ModeFast:
		return "fast"
	case ModeVoiceDetect:
		return "voice"
	default:
		return "directional"
	}
}

// ModeFromEnv reads VICTOR_MIC_MODE (directional, fast, voice).
func ModeFromEnv() Mode {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("VICTOR_MIC_MODE"))) {
	case "fast", "audio_fast_mode":
		return ModeFast
	case "voice", "voicedetect", "audio_voice_detect_mode":
		return ModeVoiceDetect
	default:
		return ModeDirectional
	}
}

// Backpack mics sit around the touch sensor. CCIS lists them rear-left,
// front-left, rear-right, front-right. Spine interleave is assumed to match
// that display order; VICTOR_MIC_ORDER can override (rl,fl,rr,fr). A wrong
// map still falls back to a delay search, which does not need labels.
//
// x is right, y is forward, metres. Spacing is the backpack top, not a lab array.
var micPos = [Channels][2]float64{
	{-0.018, -0.020}, // rear left
	{-0.018, 0.020},  // front left
	{0.018, -0.020},  // rear right
	{0.018, 0.020},   // front right
}

// Processor turns interleaved spine PCM into 16 kHz mono.
type Processor struct {
	mu     sync.Mutex
	mode   Mode
	order  [Channels]int
	hp     [Channels]biquad
	noise  [Channels]float64
	dir    int
	gain   float64
	ns     wiener
	res    resampler
	aec    echo
	primed bool
}

func NewProcessor(mode Mode) *Processor {
	p := &Processor{mode: mode, gain: 1}
	p.order = parseOrder(os.Getenv("VICTOR_MIC_ORDER"))
	for i := range p.hp {
		p.hp[i] = newHighpass(hpHz, RawRate)
		p.noise[i] = 1
	}
	p.ns.init()
	return p
}

func (p *Processor) Mode() string {
	if p == nil {
		return ModeDirectional.String()
	}
	return p.mode.String()
}

// Direction is the winning look, 0 = forward, then clockwise every 30 degrees.
func (p *Processor) Direction() int {
	if p == nil {
		return 0
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.dir
}

// NotePlayback is the speaker reference for echo cancel. PCM is 16 kHz s16le,
// the same buffer handed to aplay.
func (p *Processor) NotePlayback(pcm []byte) {
	if p == nil || len(pcm) < 2 {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.aec.pushRef(UnpackPCM(pcm))
}

// Process consumes interleaved 4-channel spine samples at RawRate.
func (p *Processor) Process(interleaved []int16) []int16 {
	if p == nil {
		return MixMono(interleaved)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	n := len(interleaved) / Channels
	if n < 8 {
		return nil
	}
	var ch [Channels][]float64
	for c := 0; c < Channels; c++ {
		ch[c] = make([]float64, n)
	}
	for i := 0; i < n; i++ {
		for c := 0; c < Channels; c++ {
			ch[p.order[c]][i] = p.hp[c].step(float64(interleaved[i*Channels+c]))
		}
	}
	for c := 0; c < Channels; c++ {
		e := energy(ch[c])
		if !p.primed {
			p.noise[c] = e
		} else if e < p.noise[c] {
			p.noise[c] = 0.80*p.noise[c] + 0.20*e
		} else {
			p.noise[c] = 0.995*p.noise[c] + 0.005*e
		}
	}
	p.primed = true

	var beam []float64
	switch p.mode {
	case ModeFast:
		beam = append([]float64(nil), ch[quietest(p.noise)]...)
	case ModeVoiceDetect:
		beam = mixVoice(ch, p.noise)
	default:
		beam, p.dir = steer(ch, p.noise)
	}
	clean := p.ns.apply(beam)
	up := p.res.push(clean)
	up = p.aec.cancel(up)
	return p.agc(up)
}

func parseOrder(s string) [Channels]int {
	order := [Channels]int{0, 1, 2, 3}
	if strings.TrimSpace(s) == "" {
		return order
	}
	name := map[string]int{"rl": 0, "fl": 1, "rr": 2, "fr": 3}
	parts := strings.Split(strings.ToLower(s), ",")
	if len(parts) != Channels {
		return order
	}
	var got [Channels]int
	seen := map[int]bool{}
	for i, part := range parts {
		id, ok := name[strings.TrimSpace(part)]
		if !ok || seen[id] {
			return order
		}
		seen[id] = true
		got[i] = id
	}
	return got
}

func quietest(noise [Channels]float64) int {
	k := 0
	for c := 1; c < Channels; c++ {
		if noise[c] < noise[k] {
			k = c
		}
	}
	return k
}

// grindMask drops a channel that is several times the median. That is the
// track mic on Vector-W1V9 (ch2 ~2e11 vs ch0 ~3e10), not a voice.
func grindMask(noise [Channels]float64) [Channels]bool {
	s := noise
	for i := 0; i < Channels; i++ {
		for j := i + 1; j < Channels; j++ {
			if s[j] < s[i] {
				s[i], s[j] = s[j], s[i]
			}
		}
	}
	med := s[1]
	if med < 1 {
		med = 1
	}
	var drop [Channels]bool
	for c := 0; c < Channels; c++ {
		if noise[c] > 4*med {
			drop[c] = true
		}
	}
	return drop
}

func mixVoice(ch [Channels][]float64, noise [Channels]float64) []float64 {
	drop := grindMask(noise)
	n := len(ch[0])
	out := make([]float64, n)
	var wsum float64
	for c := 0; c < Channels; c++ {
		if drop[c] {
			continue
		}
		w := 1 / (noise[c] + 1)
		wsum += w
		for i := 0; i < n; i++ {
			out[i] += w * ch[c][i]
		}
	}
	if wsum == 0 {
		return ch[quietest(noise)]
	}
	for i := range out {
		out[i] /= wsum
	}
	return out
}

func steer(ch [Channels][]float64, noise [Channels]float64) ([]float64, int) {
	drop := grindMask(noise)
	bestScore := -1.0
	bestDir := 0
	var best []float64
	for d := 0; d < Directions; d++ {
		az := float64(d) * 2 * math.Pi / Directions
		b := delaySum(ch, drop, lookDelays(az))
		sc := energy(b)
		if sc > bestScore {
			bestScore = sc
			bestDir = d
			best = b
		}
	}
	// Geometry can be wrong. A short delay search does not need channel labels.
	searched := delaySearch(ch, drop)
	if energy(searched) > bestScore*1.35 {
		return searched, bestDir
	}
	return best, bestDir
}

func lookDelays(az float64) [Channels]float64 {
	s, c := math.Sin(az), math.Cos(az)
	var d [Channels]float64
	min := math.MaxFloat64
	for i, p := range micPos {
		// Far-field path. Positive means the mic is closer, so it must be
		// delayed to line up with the far mic.
		d[i] = (p[0]*s + p[1]*c) / 343.0 * RawRate
		if d[i] < min {
			min = d[i]
		}
	}
	for i := range d {
		d[i] -= min
	}
	return d
}

func delaySum(ch [Channels][]float64, drop [Channels]bool, delays [Channels]float64) []float64 {
	n := len(ch[0])
	out := make([]float64, n)
	used := 0
	for c := 0; c < Channels; c++ {
		if drop[c] {
			continue
		}
		used++
		lag := delays[c]
		for i := 0; i < n; i++ {
			out[i] += at(ch[c], float64(i)-lag)
		}
	}
	if used == 0 {
		return ch[0]
	}
	scale := 1 / float64(used)
	for i := range out {
		out[i] *= scale
	}
	return out
}

func delaySearch(ch [Channels][]float64, drop [Channels]bool) []float64 {
	ref := 0
	for c := 1; c < Channels; c++ {
		if drop[ref] || (!drop[c] && energy(ch[c]) > energy(ch[ref])) {
			ref = c
		}
	}
	n := len(ch[0])
	out := make([]float64, n)
	used := 0
	for c := 0; c < Channels; c++ {
		if drop[c] {
			continue
		}
		used++
		lag := 0
		if c != ref {
			lag = bestLag(ch[ref], ch[c], 4)
		}
		for i := 0; i < n; i++ {
			j := i - lag
			if j < 0 {
				j = 0
			}
			if j >= n {
				j = n - 1
			}
			out[i] += ch[c][j]
		}
	}
	if used == 0 {
		return ch[ref]
	}
	scale := 1 / float64(used)
	for i := range out {
		out[i] *= scale
	}
	return out
}

func bestLag(ref, other []float64, max int) int {
	best, score := 0, -1.0
	n := len(ref)
	if len(other) < n {
		n = len(other)
	}
	for lag := -max; lag <= max; lag++ {
		var acc float64
		for i := max; i < n-max; i++ {
			acc += ref[i] * other[i-lag]
		}
		if acc > score {
			score = acc
			best = lag
		}
	}
	return best
}

func at(x []float64, idx float64) float64 {
	if len(x) == 0 {
		return 0
	}
	if idx <= 0 {
		return x[0]
	}
	i := int(idx)
	if i >= len(x)-1 {
		return x[len(x)-1]
	}
	f := idx - float64(i)
	return x[i]*(1-f) + x[i+1]*f
}

func energy(x []float64) float64 {
	var a float64
	for _, v := range x {
		a += v * v
	}
	return a
}

func (p *Processor) agc(x []float64) []int16 {
	if len(x) == 0 {
		return nil
	}
	var acc float64
	for _, v := range x {
		acc += v * v
	}
	rms := math.Sqrt(acc / float64(len(x)))
	if rms > 80 {
		want := targetRMS / rms
		if want > p.gain {
			p.gain = 0.90*p.gain + 0.10*want
		} else {
			p.gain = 0.98*p.gain + 0.02*want
		}
	}
	if p.gain < 0.4 {
		p.gain = 0.4
	}
	if p.gain > 12 {
		p.gain = 12
	}
	out := make([]int16, len(x))
	for i, v := range x {
		s := v * p.gain
		if s > 32767 {
			s = 32767
		} else if s < -32768 {
			s = -32768
		}
		out[i] = int16(s)
	}
	return out
}

type biquad struct {
	b0, b1, b2, a1, a2 float64
	x1, x2, y1, y2     float64
}

func newHighpass(hz, fs float64) biquad {
	w0 := 2 * math.Pi * hz / fs
	cw, sw := math.Cos(w0), math.Sin(w0)
	alpha := sw / (2 * 0.707)
	a0 := 1 + alpha
	return biquad{
		b0: ((1 + cw) / 2) / a0,
		b1: (-(1 + cw)) / a0,
		b2: ((1 + cw) / 2) / a0,
		a1: (-2 * cw) / a0,
		a2: (1 - alpha) / a0,
	}
}

func (b *biquad) step(x float64) float64 {
	y := b.b0*x + b.b1*b.x1 + b.b2*b.x2 - b.a1*b.y1 - b.a2*b.y2
	b.x2, b.x1 = b.x1, x
	b.y2, b.y1 = b.y1, y
	return y
}

// resampler is the exact 125:128 step from 15625 Hz to 16000 Hz.
type resampler struct {
	acc float64
	buf []float64
}

func (r *resampler) push(in []float64) []float64 {
	if len(in) == 0 {
		return nil
	}
	r.buf = append(r.buf, in...)
	var out []float64
	for {
		src := r.acc * 125.0 / 128.0
		i := int(src)
		if i >= len(r.buf)-1 {
			break
		}
		f := src - float64(i)
		out = append(out, r.buf[i]*(1-f)+r.buf[i+1]*f)
		r.acc++
	}
	keep := int(r.acc * 125.0 / 128.0)
	if keep > 1 {
		r.buf = append([]float64(nil), r.buf[keep-1:]...)
		r.acc -= float64(keep-1) * 128.0 / 125.0
	}
	return out
}

// wiener is a 256-point noise suppressor. Noise PSD tracks frames that sit
// near the floor, so track grind that survived the beam is attenuated and a
// louder voice frame is kept.
type wiener struct {
	noise   []float64
	primed  bool
	overlap []float64
	pending []float64
	floor   float64
}

func (w *wiener) init() {
	w.noise = make([]float64, 129)
	for i := range w.noise {
		w.noise[i] = 1
	}
}

func (w *wiener) apply(x []float64) []float64 {
	w.pending = append(w.pending, x...)
	var out []float64
	for len(w.pending) >= 256 {
		frame := w.pending[:256]
		w.pending = w.pending[128:]
		spec := rdft(frame)
		mag := make([]float64, 129)
		var e float64
		for i := 0; i < 129; i++ {
			mag[i] = spec[i].real*spec[i].real + spec[i].imag*spec[i].imag
			e += mag[i]
		}
		if !w.primed {
			copy(w.noise, mag)
			w.floor = e
			w.primed = true
		} else if e < w.floor*1.6 {
			w.floor = 0.95*w.floor + 0.05*e
			for i := range w.noise {
				w.noise[i] = 0.92*w.noise[i] + 0.08*mag[i]
			}
		} else {
			w.floor = 0.995*w.floor + 0.005*e
		}
		for i := 0; i < 129; i++ {
			g := 1 - w.noise[i]/(mag[i]+1e-6)
			if g < 0.12 {
				g = 0.12
			}
			if g > 1 {
				g = 1
			}
			spec[i].real *= g
			spec[i].imag *= g
		}
		time := irdft(spec)
		if len(w.overlap) != 128 {
			w.overlap = make([]float64, 128)
		}
		hop := make([]float64, 128)
		for i := 0; i < 128; i++ {
			win := 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/255)
			hop[i] = w.overlap[i] + time[i]*win
			w.overlap[i] = time[i+128] * (0.5 - 0.5*math.Cos(2*math.Pi*float64(i+128)/255))
		}
		out = append(out, hop...)
	}
	return out
}

type complex struct {
	real, imag float64
}

func rdft(x []float64) []complex {
	n := 256
	buf := make([]complex, n)
	for i := 0; i < n; i++ {
		buf[i].real = x[i]
	}
	fft(buf, false)
	return buf[:129]
}

func irdft(spec []complex) []float64 {
	n := 256
	buf := make([]complex, n)
	for i := 0; i < 129; i++ {
		buf[i] = spec[i]
	}
	for i := 1; i < 128; i++ {
		buf[n-i].real = spec[i].real
		buf[n-i].imag = -spec[i].imag
	}
	fft(buf, true)
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		out[i] = buf[i].real / float64(n)
	}
	return out
}

func fft(a []complex, invert bool) {
	n := len(a)
	for i, j := 1, 0; i < n; i++ {
		bit := n >> 1
		for ; j&bit != 0; bit >>= 1 {
			j ^= bit
		}
		j ^= bit
		if i < j {
			a[i], a[j] = a[j], a[i]
		}
	}
	for length := 2; length <= n; length <<= 1 {
		ang := 2 * math.Pi / float64(length)
		if !invert {
			ang = -ang
		}
		wlen := complex{math.Cos(ang), math.Sin(ang)}
		for i := 0; i < n; i += length {
			w := complex{1, 0}
			half := length >> 1
			for j := 0; j < half; j++ {
				u := a[i+j]
				v := complex{
					a[i+j+half].real*w.real - a[i+j+half].imag*w.imag,
					a[i+j+half].real*w.imag + a[i+j+half].imag*w.real,
				}
				a[i+j] = complex{u.real + v.real, u.imag + v.imag}
				a[i+j+half] = complex{u.real - v.real, u.imag - v.imag}
				w = complex{w.real*wlen.real - w.imag*wlen.imag, w.real*wlen.imag + w.imag*wlen.real}
			}
		}
	}
}

// echo subtracts the speaker reference. aplay buffering on the head is tens
// of milliseconds, so a coarse lag is searched before a short NLMS.
type echo struct {
	ref  []float64
	mic  []float64
	w    []float64
	x    []float64
	lag  int
	hold int
}

func (e *echo) pushRef(pcm []int16) {
	for _, s := range pcm {
		e.ref = append(e.ref, float64(s))
	}
	if len(e.ref) > 16000 {
		e.ref = e.ref[len(e.ref)-16000:]
	}
	e.hold = len(pcm) + 1600
}

func (e *echo) cancel(x []float64) []float64 {
	if e.hold <= 0 || len(e.ref) < 64 || len(x) == 0 {
		return x
	}
	e.mic = append(e.mic, x...)
	if len(e.mic) > 4000 {
		e.mic = e.mic[len(e.mic)-4000:]
	}
	if e.lag == 0 {
		e.lag = e.findLag()
	}
	if len(e.w) != 160 {
		e.w = make([]float64, 160)
		e.x = make([]float64, 160)
	}
	out := make([]float64, len(x))
	for i, mic := range x {
		idx := len(e.ref) - e.hold + i - e.lag
		var r float64
		if idx >= 0 && idx < len(e.ref) {
			r = e.ref[idx]
		}
		copy(e.x[1:], e.x)
		e.x[0] = r
		var y, p float64
		for k := range e.w {
			y += e.w[k] * e.x[k]
			p += e.x[k] * e.x[k]
		}
		err := mic - y
		if p > 1 {
			mu := 0.2 * err / (p + 1)
			for k := range e.w {
				e.w[k] += mu * e.x[k]
			}
		}
		out[i] = err
		e.hold--
		if e.hold <= 0 {
			e.lag = 0
			return out
		}
	}
	return out
}

func (e *echo) findLag() int {
	if len(e.mic) < 160 || len(e.ref) < 160 {
		return 80
	}
	mic := e.mic[len(e.mic)-160:]
	best, score := 40, -1.0
	max := 1600
	if max > len(e.ref)-160 {
		max = len(e.ref) - 160
	}
	for lag := 0; lag <= max; lag += 40 {
		base := len(e.ref) - 160 - lag
		if base < 0 {
			break
		}
		var acc float64
		for i := 0; i < 160; i++ {
			acc += mic[i] * e.ref[base+i]
		}
		if acc > score {
			score = acc
			best = lag
		}
	}
	return best
}
