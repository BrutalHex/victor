// Package camera grabs frames for VCT1 VIDEO.
// Real frames come from the stock camera daemon (mm-anki-camera, see anki.go);
// tests and prove-phase3 can inject a JPEG at InjectPath instead.
package camera

import (
	"bytes"
	"image"
	"image/draw"
	"image/jpeg"
	"os"
	"sync"
	"time"
)

const (
	InjectPath = "/data/victor/camera.jpg"
	OKPath     = "/data/victor/camera.ok"
	NavW       = 320
	NavH       = 180
	FaceW      = 640
	FaceH      = 360
)

var (
	mu       sync.Mutex
	lastJPEG []byte
	lastAt   time.Time
	okOnce   sync.Once

	Daemon = &Anki{}
	tone   = &Tone{Gamma: 0.8}
	toneMu sync.Mutex
)

// Start runs the camera daemon session in the background.
func Start() { go Daemon.Run(nil) }

// SetTone adjusts colour/orientation (from /data/victor/camera.conf).
func SetTone(swapRB, flip bool, gamma float64) {
	toneMu.Lock()
	tone.SwapRB, tone.Flip, tone.Gamma = swapRB, flip, gamma
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

// Snapshot returns the newest frame as full-size (640x360) and nav-size
// (320x180) images. ErrNoFrame means nothing new yet.
func Snapshot() (full, nav image.Image, err error) {
	if b, e := os.ReadFile(InjectPath); e == nil && len(b) > 32 {
		img, e := jpeg.Decode(bytes.NewReader(b))
		if e != nil {
			return nil, nil, e
		}
		return Scale(img, FaceW, FaceH), Scale(img, NavW, NavH), nil
	}
	f, err := Daemon.Latest()
	if err != nil {
		return nil, nil, err
	}
	toneMu.Lock()
	defer toneMu.Unlock()
	big, err := tone.ToRGBA(f, 1)
	if err != nil {
		return nil, nil, err
	}
	return big, halve(big), nil
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
