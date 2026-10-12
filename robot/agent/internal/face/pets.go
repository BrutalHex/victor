package face

import (
	"math"
	"time"
)

// Procedural pet faces (no close DDL asset exists): drawn with the same
// signed-distance style and teal tint as the eyes. Timed to the hub's pet
// sounds (hub/app/animals.py): the plan shows the face, the sound starts
// ~0.45 s later (0.25 s hub delay + playback start), barks 0-0.16 s and
// 0.28-0.42 s, meow 0-0.6 s, purr 0-1.4 s.
var petClips = map[string]time.Duration{
	"expr_dog": 1700 * time.Millisecond,
	"expr_cat": 1900 * time.Millisecond,
}

const petSoundAt = 0.45 // s from face start to sound start

var (
	tealSoft = hsv(0.42, 0.55, 0.95) // tongue / nose highlight
)

func petClip(name string, t time.Duration) ([]byte, bool) {
	s := t.Seconds()
	switch name {
	case "expr_dog":
		return dogFace(s), true
	case "expr_cat":
		return catFace(s), true
	}
	return nil, false
}

// pulse: 0..1 bump centred at c with half width w
func pulse(s, c, w float64) float64 {
	d := math.Abs(s-c) / w
	if d >= 1 {
		return 0
	}
	return 0.5 + 0.5*math.Cos(d*math.Pi)
}

// settle: 1 during the clip, easing to 0 over the last 0.35 s
func settle(s, length float64) float64 {
	return clamp01((length - s) / 0.35)
}

type sdf func(x, y float64) float64

// fill draws shape f (distance in px, <0 inside) in col with a soft teal glow,
// within the box x0..x1, y0..y1.
func fill(buf []byte, f sdf, col uint16, glow float64, x0, y0, x1, y1 int) {
	for y := maxi(0, y0); y <= mini(Height-1, y1); y++ {
		for x := maxi(0, x0); x <= mini(Width-1, x1); x++ {
			d := f(float64(x)+0.5, float64(y)+0.5)
			if d > glow {
				continue
			}
			var c uint16
			if d > 0 {
				if glow <= 0 {
					continue
				}
				t := 1 - d/glow
				c = mix(Black, tealGlow, t*t)
			} else {
				c = col
				if d > -1.2 {
					c = mix(Black, col, 1+d/1.2)
				}
			}
			putMax(buf, x, y, c)
		}
	}
}

// cut paints black where f < 0 (pupils, slits), anti-aliased
func cut(buf []byte, f sdf, x0, y0, x1, y1 int) {
	for y := maxi(0, y0); y <= mini(Height-1, y1); y++ {
		for x := maxi(0, x0); x <= mini(Width-1, x1); x++ {
			d := f(float64(x)+0.5, float64(y)+0.5)
			if d >= 1 {
				continue
			}
			off := (y*Width + x) * 2
			cur := uint16(buf[off])<<8 | uint16(buf[off+1])
			t := clamp01(0.5 - d)
			put(buf, x, y, mix(cur, Black, t))
		}
	}
}

func circle(cx, cy, r float64) sdf {
	return func(x, y float64) float64 { return math.Hypot(x-cx, y-cy) - r }
}

func ellipse(cx, cy, rx, ry, ang float64) sdf {
	c, s := math.Cos(ang), math.Sin(ang)
	return func(x, y float64) float64 {
		dx, dy := x-cx, y-cy
		lx, ly := dx*c+dy*s, -dx*s+dy*c
		k := math.Hypot(lx/rx, ly/ry)
		return (k - 1) * math.Min(rx, ry)
	}
}

func segment(ax, ay, bx, by, r float64) sdf {
	return func(x, y float64) float64 {
		px, py := x-ax, y-ay
		dx, dy := bx-ax, by-ay
		h := clamp01((px*dx + py*dy) / (dx*dx + dy*dy))
		return math.Hypot(px-dx*h, py-dy*h) - r
	}
}

// triangle (counter-clockwise or not) with rounded corners r
func triangle(ax, ay, bx, by, cx, cy, r float64) sdf {
	return func(x, y float64) float64 {
		e := [3][4]float64{{ax, ay, bx, by}, {bx, by, cx, cy}, {cx, cy, ax, ay}}
		d := 1e9
		sgn := 0
		for _, s := range e {
			px, py := x-s[0], y-s[1]
			dx, dy := s[2]-s[0], s[3]-s[1]
			h := clamp01((px*dx + py*dy) / (dx*dx + dy*dy))
			d = math.Min(d, math.Hypot(px-dx*h, py-dy*h))
			if dx*py-dy*px > 0 {
				sgn++
			}
		}
		if sgn == 3 || sgn == 0 {
			d = -d
		}
		return d - r
	}
}

func union(fs ...sdf) sdf {
	return func(x, y float64) float64 {
		d := 1e9
		for _, f := range fs {
			d = math.Min(d, f(x, y))
		}
		return d
	}
}

func maxi(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func mini(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// Dog: big round puppy eyes with a glint, floppy ears that flap up on each
// bark, a nose and a panting tongue.
func dogFace(s float64) []byte {
	buf := make([]byte, Bytes)
	L := petClips["expr_dog"].Seconds()
	in := clamp01(s/0.15) * settle(s, L)
	bark := pulse(s, petSoundAt+0.08, 0.12) + pulse(s, petSoundAt+0.35, 0.12)
	// eyes: grow in, squash a little on each bark
	r := 6 + 12*in
	ey := 34 - 3*bark
	for _, cx := range []float64{50, 110} {
		e := ellipse(cx, ey, r, r*(1-0.12*bark), 0)
		fill(buf, e, tealFill, 4, int(cx-r-6), int(ey-r-6), int(cx+r+6), int(ey+r+6))
		cut(buf, circle(cx+r*0.35, ey-r*0.35, r*0.22), int(cx), int(ey-r), int(cx+r), int(ey))
	}
	if in < 0.05 {
		return buf
	}
	// floppy ears hanging from the top corners, flapping up with the barks
	for i, side := range []float64{-1, 1} {
		_ = i
		bx := 80 + side*(60+4*bark)
		ang := -side * (0.5 - 0.45*bark) // hanging out-and-down, flaps up on a bark
		cy := 26 - 7*bark
		fill(buf, ellipse(bx, cy, 8*in, 19*in, ang), tealFill, 3, int(bx-28), 0, int(bx+28), 52)
	}
	// nose
	fill(buf, union(ellipse(80, 55, 9*in, 5*in, 0), triangle(72, 56, 88, 56, 80, 62, 1.5)), tealFill, 3, 64, 46, 96, 68)
	// mouth and panting tongue (after the barks)
	fill(buf, segment(80, 62, 80, 65, 1), tealFill, 0, 76, 60, 84, 68)
	fill(buf, segment(80, 65, 73, 67, 1), tealFill, 0, 70, 62, 82, 70)
	fill(buf, segment(80, 65, 87, 67, 1), tealFill, 0, 78, 62, 90, 70)
	pant := 0.0
	if s > petSoundAt+0.45 {
		pant = 0.5 + 0.5*math.Sin((s-petSoundAt-0.45)*2*math.Pi*5)
	}
	tl := (4 + 6*pant + 4*bark) * in
	fill(buf, union(ellipse(80, 66+tl/2, 5.5, tl/2+2.5, 0)), tealSoft, 2, 70, 62, 90, 79)
	return buf
}

// Cat: almond eyes with slit pupils and a slow blink, ear triangles,
// whiskers that twitch with the meow, small nose and a 'w' mouth.
func catFace(s float64) []byte {
	buf := make([]byte, Bytes)
	L := petClips["expr_cat"].Seconds()
	in := clamp01(s/0.15) * settle(s, L)
	meow := clamp01((s-petSoundAt)/0.1) * clamp01((petSoundAt+0.6-s)/0.15) // 0..1 while meowing
	// slow blink after the meow: lids close to 15% and reopen
	blink := pulse(s, petSoundAt+0.95, 0.3)
	open := in * (1 - 0.85*blink) * (1 - 0.35*meow) // eyes narrow while meowing
	for _, cx := range []float64{52, 108} {
		hx, hy := 15.0, 11*open+0.6
		// almond: intersection of two circles, approximated by a pointed ellipse
		side := 1.0
		if cx < 80 {
			side = -1
		}
		ang := side * 0.12
		e := func(x, y float64) float64 {
			dx, dy := x-cx, y-36
			c, sn := math.Cos(ang), math.Sin(ang)
			lx, ly := dx*c+dy*sn, -dx*sn+dy*c
			w := hy * (1 - math.Pow(math.Abs(lx)/hx, 1.6))
			if w < 0 {
				w = 0
			}
			return math.Max(math.Abs(lx)-hx, math.Abs(ly)-w)
		}
		fill(buf, e, tealFill, 4, int(cx-hx-6), 20, int(cx+hx+6), 52)
		if open > 0.25 {
			cut(buf, ellipse(cx, 36, 1.6+0.8*meow, hy*0.85, 0), int(cx-5), 22, int(cx+5), 50)
		}
	}
	if in < 0.05 {
		return buf
	}
	// ears
	fill(buf, triangle(22, 20, 32, 2, 46, 14, 1.5), tealFill, 3, 12, 0, 54, 28)
	fill(buf, triangle(138, 20, 128, 2, 114, 14, 1.5), tealFill, 3, 106, 0, 148, 28)
	// nose + 'w' mouth
	fill(buf, triangle(75, 52, 85, 52, 80, 57, 1), tealFill, 3, 70, 48, 90, 62)
	fill(buf, union(segment(80, 57, 80, 60, 0.9), segment(80, 60, 76, 63, 0.9), segment(76, 63, 72, 60, 0.9),
		segment(80, 60, 84, 63, 0.9), segment(84, 63, 88, 60, 0.9)), tealFill, 0, 68, 54, 92, 68)
	// whiskers: twitch while meowing / purring
	tw := 0.12*meow*math.Sin(s*2*math.Pi*9) + 0.05*math.Sin(s*2*math.Pi*1.3)
	for _, side := range []float64{-1, 1} {
		for k, a := range []float64{-0.18, 0, 0.18} {
			_ = k
			ax, ay := 80+side*12, 57.0
			aa := a + side*tw
			bx, by := ax+side*30*math.Cos(aa)*in, ay+30*math.Sin(aa)*in
			fill(buf, segment(ax, ay, bx, by, 0.8), tealFill, 1.5, int(math.Min(ax, bx))-2, int(math.Min(ay, by))-2, int(math.Max(ax, bx))+2, int(math.Max(ay, by))+2)
		}
	}
	return buf
}
