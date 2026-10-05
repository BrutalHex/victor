// Package camera grabs JPEG frames for VCT1 VIDEO.
// Hardware path is V4L2 /dev/video0 when present; tests inject a JPEG file.
package camera

import (
	"bytes"
	"image"
	"image/jpeg"
	"os"
	"sync"
	"time"
)

const (
	InjectPath = "/data/victor/camera.jpg"
	OKPath     = "/data/victor/camera.ok"
	DevPath    = "/dev/video0"
	NavW       = 320
	NavH       = 180
	FaceW      = 640
	FaceH      = 360
)

var (
	mu      sync.Mutex
	lastJPEG []byte
	lastAt   time.Time
)

func Last() []byte {
	mu.Lock()
	defer mu.Unlock()
	return lastJPEG
}

func remember(b []byte) {
	mu.Lock()
	lastJPEG = append([]byte(nil), b...)
	lastAt = time.Now()
	mu.Unlock()
	_ = os.WriteFile(OKPath, []byte("1\n"), 0644)
}

// Grab returns a JPEG. Prefers an inject file (prove-phase3), then V4L2.
func Grab() ([]byte, error) {
	if b, err := os.ReadFile(InjectPath); err == nil && len(b) > 32 {
		remember(b)
		return b, nil
	}
	b, err := grabV4L2(DevPath)
	if err != nil {
		return nil, err
	}
	remember(b)
	return b, nil
}

func ScaleJPEG(src []byte, w, h int) ([]byte, error) {
	img, err := jpeg.Decode(bytes.NewReader(src))
	if err != nil {
		return src, err
	}
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	sb := img.Bounds()
	sw, sh := sb.Dx(), sb.Dy()
	if sw <= 0 || sh <= 0 {
		return src, nil
	}
	for y := 0; y < h; y++ {
		sy := sb.Min.Y + y*sh/h
		for x := 0; x < w; x++ {
			sx := sb.Min.X + x*sw/w
			dst.Set(x, y, img.At(sx, sy))
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, dst, &jpeg.Options{Quality: 70}); err != nil {
		return src, err
	}
	return buf.Bytes(), nil
}
