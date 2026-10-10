// Package camera grabs frames for VCT1 VIDEO.
// Real frames come from the stock camera daemon (mm-anki-camera, see anki.go);
// tests and prove-phase3 can inject a JPEG at InjectPath instead.
package camera

import (
	"bytes"
	"image"
	"image/draw"
	"image/jpeg"
	"log"
	"os"
	"sync"
	"time"
)

const (
	InjectPath = "/data/victor/camera.jpg"
	OKPath     = "/data/victor/camera.ok"
	// The sensor is 4:3 (1600x1200 raw on this firmware).
	NavW  = 320
	NavH  = 240
	FaceW = 640
	FaceH = 480
)

var (
	mu       sync.Mutex
	lastJPEG []byte
	lastAt   time.Time
	okOnce   sync.Once

	Daemon = &Anki{}
	tone   = &Tone{Gamma: 0.8, Black: 16, Target: 0.22}
	toneMu sync.Mutex
)

// Start runs the camera daemon session in the background.
func Start() { go Daemon.Run(nil) }

// SetTone adjusts colour/orientation (from /data/victor/camera.conf).
func SetTone(swapRB, flip bool, gamma float64, black int) {
	toneMu.Lock()
	tone.SwapRB, tone.Flip, tone.Gamma, tone.Black = swapRB, flip, gamma, black
	toneMu.Unlock()
}

func Last() []byte {
	mu.Lock()
	defer mu.Unlock()
	return lastJPEG
}

func remember(b []byte) {
	mu.Lock()
	lastJPEG = b
	lastAt = time.Now()
	mu.Unlock()
	okOnce.Do(func() { _ = os.WriteFile(OKPath, []byte("1\n"), 0644) }) // once: no flash wear
}

// Snapshot returns the newest frame as a nav image (NavW x NavH) and, when
// wantFace, a face image (FaceW x FaceH). ErrNoFrame means nothing new yet.
func Snapshot(wantFace bool) (face, nav image.Image, err error) {
	if b, e := os.ReadFile(InjectPath); e == nil && len(b) > 32 {
		img, e := jpeg.Decode(bytes.NewReader(b))
		if e != nil {
			return nil, nil, e
		}
		return Scale(img, FaceW, FaceH), Scale(img, NavW, NavH), nil
	}
	toneMu.Lock()
	defer toneMu.Unlock()
	var perr error
	err = Daemon.WithLatest(func(f *Frame) {
		if perr = tone.Prepare(f); perr != nil {
			return
		}
		nav = tone.Render(f, NavW, NavH)
		if wantFace {
			face = tone.Render(f, FaceW, FaceH)
		}
	})
	if err == nil {
		err = perr
	}
	if err == nil {
		autoExposure.step(tone.Mean, time.Now())
	}
	return face, nav, err
}

// AE drives the sensor's manual exposure (the daemon has no auto exposure;
// the stock engine ran its own loop): keep the mean linear level near Target.
type AE struct {
	Target   float64
	ExpMs    float64
	Gain     float64
	last     time.Time
	sent     int
	Disabled bool
	send     func(ms uint16, gain float32) error
}

var autoExposure = &AE{Target: 0.28, ExpMs: 16, Gain: 1.5, send: func(ms uint16, g float32) error { return Daemon.SetExposure(ms, g) }}

// SetAE configures the exposure loop (target <= 0 disables it).
func SetDigitalTarget(target float64) {
	toneMu.Lock()
	tone.Target = target
	toneMu.Unlock()
}

func SetAE(target float64) {
	toneMu.Lock()
	defer toneMu.Unlock()
	autoExposure.Disabled = target <= 0
	if target > 0 {
		autoExposure.Target = target
	}
}

func (e *AE) step(mean float64, now time.Time) {
	if e.Disabled || e.send == nil || now.Sub(e.last) < 400*time.Millisecond {
		return
	}
	ratio := 2.0
	if mean > 0.002 {
		ratio = clampf(e.Target/mean, 0.5, 2.0)
	}
	if e.sent > 0 && ratio > 0.92 && ratio < 1.08 {
		return
	}
	total := e.ExpMs * e.Gain * ratio
	exp := clampf(total, 1, 33)
	gain := clampf(total/exp, 1, 4)
	if e.sent > 0 && exp == e.ExpMs && gain == e.Gain {
		return // at a limit
	}
	if err := e.send(uint16(exp+0.5), float32(gain)); err != nil {
		return
	}
	e.ExpMs, e.Gain, e.last = exp, gain, now
	e.sent++
	if e.sent <= 5 || e.sent%50 == 0 {
		log.Printf("camera exposure %.0fms gain %.2f (mean %.3f)", exp, gain, mean)
	}
}

// Remember records the last full JPEG (for /data/victor/camera.ok and Last()).
func Remember(b []byte) { remember(b) }

func halve(src *image.RGBA) *image.RGBA {
	b := src.Bounds()
	w, h := b.Dx()/2, b.Dy()/2
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			i0 := src.PixOffset(2*x, 2*y)
			i1 := i0 + src.Stride
			o := dst.PixOffset(x, y)
			for c := 0; c < 3; c++ {
				dst.Pix[o+c] = uint8((int(src.Pix[i0+c]) + int(src.Pix[i0+4+c]) + int(src.Pix[i1+c]) + int(src.Pix[i1+4+c])) / 4)
			}
			dst.Pix[o+3] = 255
		}
	}
	return dst
}

// Scale is a nearest-neighbour resize (inject path only).
func Scale(img image.Image, w, h int) image.Image {
	sb := img.Bounds()
	if sb.Dx() == w && sb.Dy() == h {
		return img
	}
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	src := image.NewRGBA(sb)
	draw.Draw(src, sb, img, sb.Min, draw.Src)
	sw, sh := sb.Dx(), sb.Dy()
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			copy(dst.Pix[dst.PixOffset(x, y):dst.PixOffset(x, y)+4], src.Pix[src.PixOffset(sb.Min.X+x*sw/w, sb.Min.Y+y*sh/h):])
		}
	}
	return dst
}

// ScaleJPEG is kept for callers/tests that resize a JPEG.
func ScaleJPEG(src []byte, w, h int) ([]byte, error) {
	img, err := jpeg.Decode(bytes.NewReader(src))
	if err != nil {
		return src, err
	}
	return EncodeJPEG(Scale(img, w, h), 70)
}
