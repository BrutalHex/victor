package face

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/json"
	"io"
	"math"
	"sort"
	"sync"
	"time"
)

// Stock DDL face clips: the ProceduralFaceKeyFrame tracks (eye shape, lids,
// face position) of selected animations from vector-animations-build, packed
// by tools/ddl_faceclips.py. DDL Software Asset License 1.0. Sound, head,
// lift and body tracks are not used.
//
//go:embed ddl/face_clips.json.gz
var clipsGz []byte

const ClipsTag = "victor-face-asset:ddl-face-clips"

type clipData struct {
	Src string      `json:"src"`
	Len int         `json:"len"`
	KF  [][]float64 `json:"kf"`
}

var (
	clipsOnce sync.Once
	clips     map[string]clipData
)

func loadClips() {
	clips = map[string]clipData{}
	zr, err := gzip.NewReader(bytes.NewReader(clipsGz))
	if err != nil {
		return
	}
	raw, err := io.ReadAll(zr)
	if err != nil {
		return
	}
	_ = json.Unmarshal(raw, &clips)
}

// ClipNames lists the packed clips.
func ClipNames() []string {
	clipsOnce.Do(loadClips)
	out := make([]string, 0, len(clips))
	for k := range clips {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// ClipLen is the clip duration (0 if unknown).
func ClipLen(name string) time.Duration {
	clipsOnce.Do(loadClips)
	c, ok := clips[name]
	if !ok {
		return 0
	}
	return time.Duration(c.Len) * time.Millisecond
}

const kfLen = 5 + 19 + 19

// clipParams interpolates the keyframes at t (looping if loop).
func clipParams(name string, t time.Duration, loop bool) ([]float64, bool) {
	clipsOnce.Do(loadClips)
	c, ok := clips[name]
	if !ok || len(c.KF) == 0 {
		return nil, false
	}
	ms := float64(t.Milliseconds())
	if loop && c.Len > 0 {
		ms = math.Mod(ms, float64(c.Len))
	}
	kf := c.KF
	if ms <= kf[0][0] || len(kf) == 1 {
		return kf[0], true
	}
	for i := 1; i < len(kf); i++ {
		if ms < kf[i][0] {
			a, b := kf[i-1], kf[i]
			f := (ms - a[0]) / math.Max(1, b[0]-a[0])
			out := make([]float64, kfLen)
			for j := range out {
				if j < len(a) && j < len(b) {
					out[j] = a[j] + f*(b[j]-a[j])
				}
			}
			return out, true
		}
	}
	return kf[len(kf)-1], true
}

// Clip renders clip name at t. ok=false for an unknown clip.
func Clip(name string, t time.Duration, loop bool) ([]byte, bool) {
	p, ok := clipParams(name, t, loop)
	if !ok || len(p) < kfLen {
		return nil, false
	}
	buf := make([]byte, Bytes)
	// Stock face space is 184x96; the panel is 160x80.
	sx, sy := float64(Width)/184, float64(Height)/96
	fcx, fcy, fsx, fsy := p[1]*sx, p[2]*sy, p[3], p[4]
	if fsx <= 0 {
		fsx = 1
	}
	if fsy <= 0 {
		fsy = 1
	}
	drawClipEye(buf, eyeNomLeft, p[5:24], fcx, fcy, fsx, fsy, true)
	drawClipEye(buf, eyeNomRight, p[24:43], fcx, fcy, fsx, fsy, false)
	return buf, true
}

func drawClipEye(buf []byte, nomX float64, e []float64, fcx, fcy, fsx, fsy float64, left bool) {
	cx := float64(Width)/2 + (nomX-float64(Width)/2+e[eyeCenterX])*fsx + fcx
	cy := eyeNomY + e[eyeCenterY]*fsy + fcy
	hx := eyeBaseHalfX * e[eyeScaleX] * fsx
	hy := eyeBaseHalfY * e[eyeScaleY] * fsy
	if hx < 1 || hy < 0.5 {
		return
	}
	cr := math.Min(hx, hy) * clamp01((e[eyeUpperOuterX]+e[eyeLowerOuterX]+e[eyeUpperInnerX]+e[eyeLowerInnerX])/4)
	ang := e[4] * math.Pi / 180
	upY, upAng, upBend := clamp01(e[13]), e[14]*math.Pi/180, e[15]
	loY, loAng, loBend := clamp01(e[16]), e[17]*math.Pi/180, e[18]
	if !left { // lid angles mirror on the right eye
		upAng, loAng = -upAng, -loAng
	}
	tanUp, tanLo := math.Tan(upAng)*hx/hy, math.Tan(loAng)*hx/hy
	glow := 4.0
	ext := math.Hypot(hx, hy) + glow + 1
	cosA, sinA := math.Cos(-ang), math.Sin(-ang)
	for y := int(cy - ext); y <= int(cy+ext); y++ {
		for x := int(cx - ext); x <= int(cx+ext); x++ {
			// eye-local coordinates (rotated by EyeAngle)
			dx, dy := float64(x)+0.5-cx, float64(y)+0.5-cy
			lx := dx*cosA - dy*sinA
			ly := dx*sinA + dy*cosA
			d := sdRoundBox(lx, ly, 0, 0, hx, hy, cr)
			// lids: u in [-1,1] across, v in [-1,1] top to bottom
			u := lx / hx
			uu := 1 - u*u
			if uu < 0 {
				uu = 0
			}
			v := ly / hy
			if upY > 0 || upBend != 0 {
				edge := -1 + 2*upY + tanUp*u - 6*upBend*uu
				if lid := (edge - v) * hy; lid > d {
					d = lid
				}
			}
			if loY > 0 || loBend != 0 {
				edge := 1 - 2*loY + tanLo*u - 6*loBend*uu
				if lid := (v - edge) * hy; lid > d {
					d = lid
				}
			}
			if d > glow {
				continue
			}
			var col uint16
			if d > 0 {
				t := 1 - d/glow
				col = mix(Black, tealGlow, t*t)
			} else {
				col = tealFill
				if d > -1.2 {
					col = mix(Black, col, 1+d/1.2)
				}
			}
			putMax(buf, x, y, col) // the other eye's glow never darkens this one
		}
	}
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

func putMax(buf []byte, x, y int, c uint16) {
	if x < 0 || y < 0 || x >= Width || y >= Height {
		return
	}
	off := (y*Width + x) * 2
	old := uint16(buf[off])<<8 | uint16(buf[off+1])
	_, og, _ := split565(old)
	_, ng, _ := split565(c)
	if ng >= og {
		put(buf, x, y, c)
	}
}
