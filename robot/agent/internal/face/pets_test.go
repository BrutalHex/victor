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

func TestPetClips(t *testing.T) {
	for _, n := range []string{"expr_dog", "expr_cat"} {
		if ClipLen(n) < time.Second {
			t.Fatalf("%s len %v", n, ClipLen(n))
		}
		lit := 0
		buf, ok := Clip(n, 800*time.Millisecond, false)
		if !ok || len(buf) != Bytes {
			t.Fatalf("%s not rendered", n)
		}
		for i := 0; i < len(buf); i += 2 {
			if buf[i] != 0 || buf[i+1] != 0 {
				lit++
			}
		}
		if lit < 800 {
			t.Fatalf("%s mostly dark: %d px", n, lit)
		}
		// settles to dark at the end (normal eyes come back)
		end, _ := Clip(n, ClipLen(n), false)
		on := 0
		for i := 0; i < len(end); i += 2 {
			if end[i] != 0 || end[i+1] != 0 {
				on++
			}
		}
		if on > lit/2 {
			t.Fatalf("%s does not settle: %d px", n, on)
		}
	}
}

// FACE_DUMP=dir go test -run TestDumpPets ./internal/face: stills (3x) and a sheet
func TestDumpPets(t *testing.T) {
	dir := os.Getenv("FACE_DUMP")
	if dir == "" {
		t.Skip()
	}
	for _, n := range []string{"expr_dog", "expr_cat"} {
		ts := []int{150, 500, 800, 1100, 1400}
		img := image.NewRGBA(image.Rect(0, 0, (Width*2+6)*len(ts), Height*2))
		for i, ms := range ts {
			buf, _ := Clip(n, time.Duration(ms)*time.Millisecond, false)
			for y := 0; y < Height*2; y++ {
				for x := 0; x < Width*2; x++ {
					o := ((y/2)*Width + x/2) * 2
					r, g, b := split565(uint16(buf[o])<<8 | uint16(buf[o+1]))
					img.Set(i*(Width*2+6)+x, y, color.RGBA{r, g, b, 255})
				}
			}
		}
		f, _ := os.Create(filepath.Join(dir, n+"_sheet.png"))
		_ = png.Encode(f, img)
		f.Close()
		buf, _ := Clip(n, 800*time.Millisecond, false)
		one := image.NewRGBA(image.Rect(0, 0, Width*4, Height*4))
		for y := 0; y < Height*4; y++ {
			for x := 0; x < Width*4; x++ {
				o := ((y/4)*Width + x/4) * 2
				r, g, b := split565(uint16(buf[o])<<8 | uint16(buf[o+1]))
				one.Set(x, y, color.RGBA{r, g, b, 255})
			}
		}
		f, _ = os.Create(filepath.Join(dir, n+".png"))
		_ = png.Encode(f, one)
		f.Close()
	}
}
