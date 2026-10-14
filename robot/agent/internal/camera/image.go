package camera

import (
	"bytes"
	"errors"
	"image"
	"image/jpeg"
	"math"
)

const (
	FormatRAW2MP    = 3 // BGGR10 MIPI-packed, 1600x1200 (this firmware's default)
	FormatRGB888_2M = 4
)

// Tone turns sensor frames into balanced RGB. Raw frames are a straight
// sensor readout (no ISP white balance or gamma): subtract the black level,
// apply gray-world white balance (smoothed across frames) and a gamma lift.
type Tone struct {
	SwapRB bool
	Flip   bool    // rotate 180 degrees
	Gamma  float64 // <1 brightens shadows; 0 or 1 = linear
	Black  int     // black level on the 8-bit scale (raw only)
	gains  [3]float64
	lut    [3][256]uint8
	Mean   float64 // mean linear level of the last prepared frame, 0..1 (before WB)
	Target float64 // digital gain aims the mean here (0 = no digital gain)
	dgain  float64
	rows   [][]byte // cached copies of raw rows for the current frame
	rowGen []uint32
	gen    uint32
	gtab   [1025]uint8 // gamma curve sampled on [0,1]
	gtabG  float64
	cols   []int32 // per output column: packed byte index of the B pixel, for (W, ow)
	colsW  [2]int
}

// gammaAt maps v in [0,1] through the gamma curve (table built once per gamma).
func (t *Tone) gammaAt(v float64) uint8 {
	if t.gtabG != t.Gamma || t.gtab[1024] == 0 {
		for i := range t.gtab {
			x := float64(i) / 1024
			if t.Gamma > 0 && t.Gamma != 1 {
				x = math.Pow(x, t.Gamma)
			}
			t.gtab[i] = uint8(x*255 + 0.5)
		}
		t.gtabG = t.Gamma
	}
	return t.gtab[int(v*1024+0.5)]
}

// row returns a cached copy of raw row y: the ION buffer is uncached, so one
// bulk copy per row is much cheaper than many single-byte reads.
func (t *Tone) row(f *Frame, y int) []byte {
	if len(t.rows) < f.H {
		t.rows = make([][]byte, f.H)
		t.rowGen = make([]uint32, f.H)
	}
	if t.rowGen[y] != t.gen || len(t.rows[y]) != f.Stride {
		if cap(t.rows[y]) < f.Stride {
			t.rows[y] = make([]byte, f.Stride)
		}
		t.rows[y] = t.rows[y][:f.Stride]
		copy(t.rows[y], f.Data[y*f.Stride:(y+1)*f.Stride])
		t.rowGen[y] = t.gen
	}
	return t.rows[y]
}

func (t *Tone) buildLUTs(sum [3]float64) {
	g := [3]float64{1, 1, 1}
	if sum[0] > 0 && sum[1] > 0 && sum[2] > 0 {
		avg := (sum[0] + sum[1] + sum[2]) / 3
		for i := range g {
			g[i] = clampf(avg/sum[i], 0.4, 3.0)
		}
	}
	if t.gains[1] == 0 {
		t.gains = g
	} else {
		for i := range g {
			t.gains[i] = 0.8*t.gains[i] + 0.2*g[i]
		}
	}
	dg := 1.0
	if t.Target > 0 && t.Mean > 0.001 {
		dg = clampf(t.Target/t.Mean, 1, 8)
	}
	if t.dgain == 0 {
		t.dgain = dg
	} else {
		t.dgain = 0.7*t.dgain + 0.3*dg
	}
	for c := 0; c < 3; c++ {
		for i := 0; i < 256; i++ {
			v := float64(i-t.Black) / float64(255-t.Black)
			if v < 0 {
				v = 0
			}
			v *= t.gains[c] * t.dgain
			if v > 1 {
				v = 1
			}
			t.lut[c][i] = t.gammaAt(v)
		}
	}
}

// Prepare updates white balance / LUTs from a sparse sample of the frame.
func (t *Tone) Prepare(f *Frame) error {
	t.gen++
	var sum [3]float64
	switch f.Format {
	case FormatRGB888, FormatRGB888_2M:
		if f.Stride < f.W*3 || len(f.Data) < f.Stride*f.H {
			return errors.New("camera: short RGB frame")
		}
		for y := 0; y < f.H; y += 8 {
			row := f.Data[y*f.Stride:]
			for x := 0; x < f.W; x += 8 {
				p := row[x*3:]
				sum[0] += math.Max(float64(int(p[0])-t.Black), 0)
				sum[1] += math.Max(float64(int(p[1])-t.Black), 0)
				sum[2] += math.Max(float64(int(p[2])-t.Black), 0)
			}
		}
	case FormatRAW, FormatRAW2MP:
		if f.Stride < f.W*10/8 || len(f.Data) < f.Stride*f.H {
			return errors.New("camera: short RAW frame")
		}
		for y := 0; y+1 < f.H; y += 12 {
			r0 := f.Data[y*f.Stride:]
			r1 := f.Data[(y+1)*f.Stride:]
			for x := 0; x+1 < f.W; x += 12 {
				sum[2] += math.Max(float64(int(rawAt(r0, x))-t.Black), 0)
				sum[1] += math.Max(float64(int(rawAt(r0, x+1))+int(rawAt(r1, x)))/2-float64(t.Black), 0)
				sum[0] += math.Max(float64(int(rawAt(r1, x+1))-t.Black), 0)
			}
		}
	default:
		return errors.New("camera: unsupported frame format")
	}
	if total := sum[0] + sum[1] + sum[2]; total > 0 {
		n := 0.0
		switch f.Format {
		case FormatRAW, FormatRAW2MP:
			n = float64(((f.H-1)/12 + 1) * ((f.W-1)/12 + 1))
		default:
			n = float64(((f.H-1)/8 + 1) * ((f.W-1)/8 + 1))
		}
		t.Mean = total / 3 / n / float64(255-t.Black)
	} else {
		t.Mean = 0
	}
	if t.SwapRB {
		sum[0], sum[2] = sum[2], sum[0]
	}
	t.buildLUTs(sum)
	return nil
}

// Render samples the frame into an ow x oh image (nearest Bayer quad / pixel).
// Call Prepare first.
func (t *Tone) Render(f *Frame, ow, oh int) *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, ow, oh))
	raw := f.Format == FormatRAW || f.Format == FormatRAW2MP
	xs := make([]int, ow)
	for x := range xs {
		sx := x * f.W / ow
		if raw {
			sx &^= 1
		}
		xs[x] = sx
	}
	lr, lg, lb := &t.lut[0], &t.lut[1], &t.lut[2]
	if raw {
		// byte offsets of the B (even) pixel; the odd pixel is the next byte
		// unless the even one is the 4th in its 5-byte group (never: sx is even,
		// so sx%4 is 0 or 2 and sx+1 stays in the same group).
		if t.colsW != [2]int{f.W, ow} {
			t.cols = make([]int32, ow)
			for x, sx := range xs {
				t.cols[x] = int32(sx/4*5 + sx%4)
			}
			t.colsW = [2]int{f.W, ow}
		}
		cols := t.cols
		for y := 0; y < oh; y++ {
			sy := (y * f.H / oh) &^ 1
			ty := y
			if t.Flip {
				ty = oh - 1 - y
			}
			out := img.Pix[ty*img.Stride : (ty+1)*img.Stride]
			r0 := t.row(f, sy)
			r1 := t.row(f, sy+1)
			if t.SwapRB { // raw B -> output red, raw R -> output blue
				for x, i := range cols {
					tx := x
					if t.Flip {
						tx = ow - 1 - x
					}
					o := out[tx*4 : tx*4+4 : tx*4+4]
					o[0] = lr[r0[i]]
					o[1] = lg[uint8((uint16(r0[i+1])+uint16(r1[i]))>>1)]
					o[2] = lb[r1[i+1]]
					o[3] = 255
				}
				continue
			}
			if t.Flip {
				for x, i := range cols {
					o := (ow - 1 - x) * 4
					out[o] = lr[r1[i+1]]
					out[o+1] = lg[uint8((uint16(r0[i+1])+uint16(r1[i]))>>1)]
					out[o+2] = lb[r0[i]]
					out[o+3] = 255
				}
				continue
			}
			for x, i := range cols {
				o := out[x*4 : x*4+4 : x*4+4]
				o[0] = lr[r1[i+1]]
				o[1] = lg[uint8((uint16(r0[i+1])+uint16(r1[i]))>>1)]
				o[2] = lb[r0[i]]
				o[3] = 255
			}
		}
		return img
	}
	for y := 0; y < oh; y++ {
		sy := y * f.H / oh
		ty := y
		if t.Flip {
			ty = oh - 1 - y
		}
		out := img.Pix[ty*img.Stride:]
		row := f.Data[sy*f.Stride:]
		for x, sx := range xs {
			p := row[sx*3:]
			r, g, b := p[0], p[1], p[2]
			if t.SwapRB {
				r, b = b, r
			}
			tx := x
			if t.Flip {
				tx = ow - 1 - x
			}
			o := out[tx*4:]
			o[0], o[1], o[2], o[3] = lr[r], lg[g], lb[b], 255
		}
	}
	return img
}

// Convert renders at the natural size: raw at half resolution (2x2 quads),
// RGB at full size.
func (t *Tone) Convert(f *Frame) (*image.RGBA, error) {
	if err := t.Prepare(f); err != nil {
		return nil, err
	}
	if f.Format == FormatRAW || f.Format == FormatRAW2MP {
		return t.Render(f, f.W/2, f.H/2), nil
	}
	return t.Render(f, f.W, f.H), nil
}

// ToRGBA is kept for tests: convert, then box-shrink by factor.
func (t *Tone) ToRGBA(f *Frame, factor int) (*image.RGBA, error) {
	img, err := t.Convert(f)
	if err != nil || factor <= 1 {
		return img, err
	}
	for factor > 1 {
		img = halve(img)
		factor /= 2
	}
	return img, nil
}

// rawAt returns the 8 high bits of pixel x in a MIPI RAW10 row (4 px / 5 bytes).
func rawAt(row []byte, x int) uint8 { return row[x/4*5+x%4] }

func EncodeJPEG(img image.Image, q int) ([]byte, error) {
	var buf bytes.Buffer
	err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: q})
	return buf.Bytes(), err
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
