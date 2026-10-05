package face

import (
	_ "embed"
	"encoding/binary"
	"encoding/json"
	"math"
)

// Default eye pose from Digital Dream Labs vector-animations-build
// assets/animations/anim_eyeposes_01/anim_eyepose_default.json.
// Parameter order is ProceduralFaceParams in scripts/exampleScripts/modifyEyes.py.
//
//go:embed ddl/anim_eyepose_default.json
var eyeposeDefault []byte

const (
	eyeCenterX = 0
	eyeCenterY = 1
	eyeScaleX  = 2
	eyeScaleY  = 3
	eyeLowerInnerX = 5
	eyeLowerInnerY = 6
	eyeUpperInnerX = 7
	eyeUpperInnerY = 8
	eyeUpperOuterX = 9
	eyeUpperOuterY = 10
	eyeLowerOuterX = 11
	eyeLowerOuterY = 12
	// Nominal half-size before EyeScale. Centers sit ±46 px from the panel center;
	// EyeCenterX/Y from the pack are added on top.
	eyeBaseHalfX = 18.0
	eyeBaseHalfY = 20.0
	eyeNomLeft   = 40.0
	eyeNomRight  = 120.0
	eyeNomY      = 40.0
)

func EyesFrame(lookX, lookY, blink float64) []byte {
	return renderEyes(lookX, lookY, blink, 1)
}

func EyesThinking(phase float64) []byte {
	look := 0.35 * math.Sin(phase*2*math.Pi)
	return renderEyes(look, 0.08, 0.22, 0.85)
}

func EyesCaption(text string, fg uint16) []byte {
	buf := renderEyes(0, 0, 0, 1)
	blitText(buf, text, fg, 24)
	return buf
}

func renderEyes(lookX, lookY, blink, dim float64) []byte {
	buf := make([]byte, Bytes)
	left, right := defaultEyes()
	if blink < 0 {
		blink = 0
	}
	if blink > 1 {
		blink = 1
	}
	open := 1 - 0.88*blink
	if open < 0.10 {
		open = 0.10
	}
	drawPackEye(buf, eyeNomLeft, left, lookX, lookY, open, dim, true)
	drawPackEye(buf, eyeNomRight, right, lookX, lookY, open, dim, false)
	return buf
}

func defaultEyes() ([]float64, []float64) {
	var doc map[string][]struct {
		Left  []float64 `json:"leftEye"`
		Right []float64 `json:"rightEye"`
	}
	if err := json.Unmarshal(eyeposeDefault, &doc); err != nil {
		return fallbackEye(), fallbackEye()
	}
	for _, frames := range doc {
		if len(frames) > 0 && len(frames[0].Left) > 12 && len(frames[0].Right) > 12 {
			return frames[0].Left, frames[0].Right
		}
	}
	return fallbackEye(), fallbackEye()
}

func fallbackEye() []float64 {
	e := make([]float64, 25)
	e[eyeScaleX], e[eyeScaleY] = 1.52, 1.14
	e[eyeLowerInnerX], e[eyeLowerInnerY] = 0.48, 0.46
	e[eyeUpperInnerX], e[eyeUpperInnerY] = 0.43, 0.52
	e[eyeUpperOuterX], e[eyeUpperOuterY] = 0.56, 0.61
	e[eyeLowerOuterX], e[eyeLowerOuterY] = 0.52, 0.57
	return e
}

func drawPackEye(buf []byte, nomX float64, p []float64, lookX, lookY, open, dim float64, left bool) {
	cx := nomX + p[eyeCenterX] + lookX*8
	cy := eyeNomY + p[eyeCenterY] + lookY*6
	hx := eyeBaseHalfX * p[eyeScaleX]
	hy := eyeBaseHalfY * p[eyeScaleY] * open
	if hx < 2 {
		hx = 2
	}
	if hy < 2 {
		hy = 2
	}
	var li, lo float64
	if left {
		li, lo = p[eyeLowerInnerX], p[eyeLowerOuterX]
	} else {
		li, lo = p[eyeLowerOuterX], p[eyeLowerInnerX]
	}
	_ = li
	_ = lo
	cr := math.Min(hx, hy) * ((p[eyeUpperOuterX] + p[eyeLowerOuterX] + p[eyeUpperInnerX] + p[eyeLowerInnerX]) / 4)
	glow := 4.0
	x0 := int(cx - hx - glow - 1)
	x1 := int(cx + hx + glow + 1)
	y0 := int(cy - hy - glow - 1)
	y1 := int(cy + hy + glow + 1)
	for y := y0; y <= y1; y++ {
		for x := x0; x <= x1; x++ {
			d := sdRoundBox(float64(x)+0.5, float64(y)+0.5, cx, cy, hx, hy, cr)
			if d > glow {
				continue
			}
			var col uint16
			if d > 0 {
				t := 1 - d/glow
				col = mix(Black, tealGlow, t*t*dim)
			} else {
				body := tealFill
				if d > -1.2 {
					body = mix(Black, body, 1+d/1.2)
				}
				col = scaleRGB(body, dim)
			}
			put(buf, x, y, col)
		}
	}
}

func sdRoundBox(px, py, cx, cy, hx, hy, r float64) float64 {
	dx := math.Abs(px-cx) - (hx - r)
	dy := math.Abs(py-cy) - (hy - r)
	ax, ay := dx, dy
	if ax < 0 {
		ax = 0
	}
	if ay < 0 {
		ay = 0
	}
	outside := math.Hypot(ax, ay)
	inside := dx
	if dy > inside {
		inside = dy
	}
	if inside > 0 {
		inside = 0
	}
	return outside + inside - r
}

var (
	tealFill = hsv(0.42, 1, 0.92)
	tealGlow = hsv(0.42, 1, 0.16)
)

func hsv(h, s, v float64) uint16 {
	i := math.Floor(h * 6)
	f := h*6 - i
	p := v * (1 - s)
	q := v * (1 - f*s)
	t := v * (1 - (1-f)*s)
	var r, g, b float64
	switch int(i) % 6 {
	case 0:
		r, g, b = v, t, p
	case 1:
		r, g, b = q, v, p
	case 2:
		r, g, b = p, v, t
	case 3:
		r, g, b = p, q, v
	case 4:
		r, g, b = t, p, v
	default:
		r, g, b = v, p, q
	}
	return RGB565(uint8(r*255+0.5), uint8(g*255+0.5), uint8(b*255+0.5))
}

func mix(a, b uint16, t float64) uint16 {
	if t <= 0 {
		return a
	}
	if t >= 1 {
		return b
	}
	ar, ag, ab := split565(a)
	br, bg, bb := split565(b)
	return RGB565(
		uint8(float64(ar)+t*(float64(br)-float64(ar))+0.5),
		uint8(float64(ag)+t*(float64(bg)-float64(ag))+0.5),
		uint8(float64(ab)+t*(float64(bb)-float64(ab))+0.5),
	)
}

func scaleRGB(c uint16, s float64) uint16 {
	if s >= 1 {
		return c
	}
	r, g, b := split565(c)
	return RGB565(uint8(float64(r)*s), uint8(float64(g)*s), uint8(float64(b)*s))
}

func split565(c uint16) (uint8, uint8, uint8) {
	return uint8((c>>11)&0x1f) << 3, uint8((c>>5)&0x3f) << 2, uint8(c&0x1f) << 3
}

func put(buf []byte, x, y int, c uint16) {
	if x < 0 || y < 0 || x >= Width || y >= Height {
		return
	}
	off := (y*Width + x) * 2
	binary.BigEndian.PutUint16(buf[off:], c)
}
