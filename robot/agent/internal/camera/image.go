package camera

import (
	"bytes"
	"errors"
	"image"
	"image/jpeg"
	"math"
)

// Tuning for the raw RGB888 frames from mm-anki-camera: they are a straight
// debayer of the sensor (no ISP white balance or gamma), so apply a gray-world
// white balance (gains smoothed across frames) and a mild gamma lift.
type Tone struct {
	SwapRB  bool
	Flip    bool    // rotate 180 degrees
	Gamma   float64 // <1 brightens shadows; 0 = off
	gains   [3]float64
	lut     [256]uint8
	lutFor  float64
}

func (t *Tone) ensureLUT() {
	if t.lutFor == t.Gamma && t.lut[255] != 0 {
		return
	}
	for i := range t.lut {
		v := float64(i) / 255
		if t.Gamma > 0 {
			v = math.Pow(v, t.Gamma)
		}
		t.lut[i] = uint8(v*255 + 0.5)
	}
	t.lutFor = t.Gamma
}

// ToRGBA converts an RGB888 frame, scaling down by an integer box factor.
func (t *Tone) ToRGBA(f *Frame, factor int) (*image.RGBA, error) {
	if f.Format != FormatRGB888 {
		return nil, errors.New("camera: only RGB888 frames are supported")
	}
	if factor < 1 {
		factor = 1
	}
	t.ensureLUT()
	ow, oh := f.W/factor, f.H/factor
	// gray-world gains from a sparse sample
	var sum [3]float64
	for y := 0; y < f.H; y += 8 {
		row := f.Data[y*f.Stride:]
		for x := 0; x < f.W; x += 8 {
			p := row[x*3:]
			sum[0] += float64(p[0])
			sum[1] += float64(p[1])
			sum[2] += float64(p[2])
		}
	}
	if t.SwapRB {
		sum[0], sum[2] = sum[2], sum[0]
	}
	g := [3]float64{1, 1, 1}
	if sum[0] > 0 && sum[1] > 0 && sum[2] > 0 {
		avg := (sum[0] + sum[1] + sum[2]) / 3
		for i := range g {
			g[i] = clampf(avg/sum[i], 0.5, 2.5)
		}
	}
	if t.gains[1] == 0 {
		t.gains = g
	} else {
		for i := range g {
			t.gains[i] = 0.8*t.gains[i] + 0.2*g[i]
		}
	}
	img := image.NewRGBA(image.Rect(0, 0, ow, oh))
	area := factor * factor
	for oy := 0; oy < oh; oy++ {
		for ox := 0; ox < ow; ox++ {
			var r, gg, b int
			for dy := 0; dy < factor; dy++ {
				row := f.Data[(oy*factor+dy)*f.Stride:]
				for dx := 0; dx < factor; dx++ {
					p := row[(ox*factor+dx)*3:]
					r += int(p[0])
					gg += int(p[1])
					b += int(p[2])
				}
			}
			if t.SwapRB {
				r, b = b, r
			}
			tx, ty := ox, oy
			if t.Flip {
				tx, ty = ow-1-ox, oh-1-oy
			}
			i := img.PixOffset(tx, ty)
			img.Pix[i+0] = t.lut[clamp8(float64(r)/float64(area)*t.gains[0])]
			img.Pix[i+1] = t.lut[clamp8(float64(gg)/float64(area)*t.gains[1])]
			img.Pix[i+2] = t.lut[clamp8(float64(b)/float64(area)*t.gains[2])]
			img.Pix[i+3] = 255
		}
	}
	return img, nil
}

func EncodeJPEG(img image.Image, q int) ([]byte, error) {
	var buf bytes.Buffer
	err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: q})
	return buf.Bytes(), err
}

func clamp8(v float64) int {
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return int(v)
}

func clampf(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

