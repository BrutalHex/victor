package face

import (
	"encoding/binary"
	"math"
)

// Anki default eye color is Teal: hue=0.42, saturation=1.00
// (vector-python-sdk behavior.set_eye_color). Vector 2.0 keeps that
// character: two glowing rounded-rect “pills” on black, no iris/pupil.
// Dei Gaztelumendi’s brief was fennec-fox curiosity, not a cartoon eyeball.

func hsv(h, s, v float64) uint16 {
	for h < 0 {
		h += 1
	}
	for h >= 1 {
		h -= 1
	}
	if s < 0 {
		s = 0
	}
	if s > 1 {
		s = 1
	}
	if v < 0 {
		v = 0
	}
	if v > 1 {
		v = 1
	}
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

var (
	// Official default teal, plus the glow/highlight stops of the original face.
	tealHue = 0.42
	tealFill = hsv(tealHue, 1.00, 0.92)
	tealHot  = hsv(tealHue, 0.55, 1.00)
	tealRim  = hsv(tealHue, 1.00, 0.38)
	tealGlow = hsv(tealHue, 1.00, 0.16)
)

func EyesFrame(lookX, lookY, blink float64) []byte {
	return renderEyes(lookX, lookY, blink, 1.0)
}

func EyesThinking(phase float64) []byte {
	// Original Vector “thinking” is a slight squint and a slow look, not a UI bar.
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
	if blink < 0 {
		blink = 0
	}
	if blink > 1 {
		blink = 1
	}
	open := 1.0 - 0.88*blink
	if open < 0.10 {
		open = 0.10
	}
	// Vector 1.0 Santek 184×96: two stadiums, close-set, vertically centered.
	hx, hy := 31.0, 23.0*open
	cr := math.Min(hx, hy) * 0.92
	shiftX := lookX * 10
	shiftY := lookY * 6
	drawGlowingEye(buf, 53+shiftX, 48+shiftY, hx, hy, cr, dim)
	drawGlowingEye(buf, 131+shiftX, 48+shiftY, hx, hy, cr, dim)
	return buf
}

func drawGlowingEye(buf []byte, cx, cy, hx, hy, cr, dim float64) {
	glow := 5.0
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
			switch {
			case d > 0:
				// Soft outer halo — Vector’s LCD always has a faint bloom.
				t := 1 - d/glow
				t *= t
				col = mix(Black, tealGlow, t*dim)
			default:
				// Inside: rim → fill → hot highlight in the upper-left.
				nx := (float64(x)+0.5 - cx) / hx
				ny := (float64(y)+0.5 - cy) / hy
				rad := math.Min(1, math.Hypot(nx, ny))
				body := mix(tealFill, tealRim, rad*rad*0.55)
				// Specular: small bright oval, upper-left, like the original catchlight.
				hxlt := (float64(x)+0.5 - (cx - hx*0.32)) / (hx * 0.38)
				hylt := (float64(y)+0.5 - (cy - hy*0.38)) / (hy * 0.32)
				hl := hxlt*hxlt + hylt*hylt
				if hl < 1 {
					body = mix(tealHot, body, math.Sqrt(hl))
				}
				// Anti-alias the rounded edge into black.
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
	r := uint8((c>>11)&0x1f) << 3
	g := uint8((c>>5)&0x3f) << 2
	b := uint8(c&0x1f) << 3
	return r, g, b
}

func put(buf []byte, x, y int, c uint16) {
	if x < 0 || y < 0 || x >= Width || y >= Height {
		return
	}
	off := (y*Width + x) * 2
	binary.BigEndian.PutUint16(buf[off:], c)
}
