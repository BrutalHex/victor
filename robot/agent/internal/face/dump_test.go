package face

import (
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDumpClips(t *testing.T) {
	dir := os.Getenv("FACE_DUMP")
	if dir == "" {
		t.Skip()
	}
	for _, c := range []struct {
		n  string
		at time.Duration
	}{{"petting_bliss", time.Second}, {"petting_lvl3", 2 * time.Second}, {"iloveyou", 1500 * time.Millisecond}, {"goodnight", 2000 * time.Millisecond}, {"sleeping", 3 * time.Second}, {"fistbump_success", 1500 * time.Millisecond}, {"shutup", 2 * time.Second}, {"hello", 1500 * time.Millisecond}} {
		buf, _ := Clip(c.n, c.at, true)
		img := image.NewRGBA(image.Rect(0, 0, Width*3, Height*3))
		for y := 0; y < Height; y++ {
			for x := 0; x < Width; x++ {
				v := uint16(buf[(y*Width+x)*2])<<8 | uint16(buf[(y*Width+x)*2+1])
				r, g, b := split565(v)
				for dy := 0; dy < 3; dy++ {
					for dx := 0; dx < 3; dx++ {
						img.Set(x*3+dx, y*3+dy, color.RGBA{r, g, b, 255})
					}
				}
			}
		}
		f, _ := os.Create(filepath.Join(dir, c.n+".png"))
		_ = png.Encode(f, img)
		f.Close()
	}
}

// FACE_DUMP=dir FACE_DUMP_SHEET=1 go test -run TestDumpSheet ./internal/face:
// one PNG per clip with 5 frames across its length (expression previews).
func TestDumpSheet(t *testing.T) {
	dir := os.Getenv("FACE_DUMP")
	if dir == "" || os.Getenv("FACE_DUMP_SHEET") == "" {
		t.Skip()
	}
	for _, n := range ClipNames() {
		l := ClipLen(n)
		const k = 5
		img := image.NewRGBA(image.Rect(0, 0, (Width+4)*k, Height))
		for i := 0; i < k; i++ {
			buf, _ := Clip(n, l*time.Duration(i)/time.Duration(k-1)*9/10, false)
			for y := 0; y < Height; y++ {
				for x := 0; x < Width; x++ {
					v := uint16(buf[(y*Width+x)*2])<<8 | uint16(buf[(y*Width+x)*2+1])
					r, g, b := split565(v)
					img.Set(i*(Width+4)+x, y, color.RGBA{r, g, b, 255})
				}
			}
		}
		f, _ := os.Create(filepath.Join(dir, n+".png"))
		_ = png.Encode(f, img)
		f.Close()
	}
}
