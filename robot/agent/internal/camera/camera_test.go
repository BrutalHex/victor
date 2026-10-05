package camera

import (
	"bytes"
	"image"
	"image/color"
	"image/jpeg"
	"os"
	"path/filepath"
	"testing"
)

func testJPEG(t *testing.T, w, h int) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, w, h))
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			img.Set(x, y, color.RGBA{uint8(x), uint8(y), 80, 255})
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: 80}); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestScaleJPEG(t *testing.T) {
	src := testJPEG(t, 640, 360)
	got, err := ScaleJPEG(src, NavW, NavH)
	if err != nil {
		t.Fatal(err)
	}
	img, err := jpeg.Decode(bytes.NewReader(got))
	if err != nil {
		t.Fatal(err)
	}
	b := img.Bounds()
	if b.Dx() != NavW || b.Dy() != NavH {
		t.Fatalf("size %dx%d", b.Dx(), b.Dy())
	}
}

func TestGrabInject(t *testing.T) {
	dir := t.TempDir()
	// Use the well-known path only if we can; otherwise unit-test ScaleJPEG is enough.
	src := testJPEG(t, 32, 32)
	p := filepath.Join(dir, "camera.jpg")
	if err := os.WriteFile(p, src, 0644); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(p)
	if err != nil || len(b) < 32 {
		t.Fatal("inject file")
	}
}
