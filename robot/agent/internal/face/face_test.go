package face

import "testing"

func TestFrameSize(t *testing.T) {
	f := EyesFrame(0, 0, 0)
	if len(f) != Bytes || Bytes != 184*96*2 {
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

func TestThinkingBar(t *testing.T) {
	f := ThinkingFrame(0.5)
	if len(f) != Bytes {
		t.Fatalf("len %d", len(f))
	}
	on := 0
	for i := 0; i < len(f); i += 2 {
		if f[i] != 0 || f[i+1] != 0 {
			on++
		}
	}
	if on < 100 {
		t.Fatalf("bar too empty %d", on)
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
