package face

import (
	"bytes"
	"testing"
	"time"
)

func TestFrameSize(t *testing.T) {
	f := EyesFrame(0, 0, 0)
	if len(f) != Bytes || Bytes != 160*80*2 {
		t.Fatalf("len %d want %d", len(f), Bytes)
	}
}

func TestEyesDrawn(t *testing.T) {
	f := EyesFrame(0, 0, 0)
	on := 0
	for i := 0; i < len(f); i += 2 {
		if f[i] != 0 || f[i+1] != 0 {
			on++
		}
	}
	if on < 800 {
		t.Fatalf("eyes too empty %d", on)
	}
}

func TestBlinkShrinks(t *testing.T) {
	open := countLit(EyesFrame(0, 0, 0))
	shut := countLit(EyesFrame(0, 0, 1))
	if shut >= open {
		t.Fatalf("blink should shrink eyes open=%d shut=%d", open, shut)
	}
}

func countLit(f []byte) int {
	n := 0
	for i := 0; i < len(f); i += 2 {
		if f[i] != 0 || f[i+1] != 0 {
			n++
		}
	}
	return n
}

func TestThinkingReplacesEyes(t *testing.T) {
	nIn, nLoop := SearchingFrames()
	if nIn != 8 || nLoop != 36 {
		t.Fatalf("DDL searching frames getin=%d loop=%d", nIn, nLoop)
	}
	idle := EyesFrame(0, 0, 0)
	seen := map[string]bool{}
	for ms := 0; ms < 2000; ms += 80 {
		f := ThinkingFrame(time.Duration(ms) * time.Millisecond)
		if len(f) != Bytes {
			t.Fatalf("len %d", len(f))
		}
		diff, bright, lit := 0, 0, 0
		minX, maxX, minY, maxY := Width, 0, Height, 0
		for i := 0; i < len(f); i += 2 {
			c := uint16(f[i])<<8 | uint16(f[i+1])
			if f[i] != idle[i] || f[i+1] != idle[i+1] {
				diff++
			}
			if c == 0 {
				continue
			}
			lit++
			r, g, b := split565(c)
			if int(r)+int(g)+int(b) > 300 {
				bright++
			}
			x, y := (i/2)%Width, (i/2)/Width
			minX, maxX = min(minX, x), max(maxX, x)
			minY, maxY = min(minY, y), max(maxY, y)
		}
		if diff < Width*Height/5 {
			t.Fatalf("%d ms: only %d px differ from idle eyes", ms, diff)
		}
		if bright < 60 || lit < 600 {
			t.Fatalf("%d ms: not visible bright=%d lit=%d", ms, bright, lit)
		}
		inLoop := ms >= nIn*1000/SpriteFPS
		if inLoop && (maxX-minX < 100 || maxY-minY < 30) {
			t.Fatalf("%d ms: content box x %d-%d y %d-%d", ms, minX, maxX, minY, maxY)
		}
		seen[string(f)] = true
	}
	if len(seen) < 15 {
		t.Fatalf("animation not moving: %d distinct frames in 2 s", len(seen))
	}
	// Loop wraps at getin + 36 frames of 1/30 s.
	a := ThinkingFrame(time.Duration(nIn)*time.Second/SpriteFPS + time.Millisecond)
	b := ThinkingFrame(time.Duration(nIn+nLoop)*time.Second/SpriteFPS + time.Millisecond)
	if !bytes.Equal(a, b) {
		t.Fatal("loop does not wrap")
	}
}

func TestNameGlyphs(t *testing.T) {
	f := Frame("MOHAMMAD", Green)
	on := 0
	for i := 0; i < len(f); i += 2 {
		if f[i] != 0 || f[i+1] != 0 {
			on++
		}
	}
	if on < 80 {
		t.Fatalf("name not drawn %d", on)
	}
}
