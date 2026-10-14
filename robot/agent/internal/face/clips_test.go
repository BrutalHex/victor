package face

import (
	"testing"
	"time"
)

func lit(buf []byte) int {
	n := 0
	for i := 0; i+1 < len(buf); i += 2 {
		if buf[i] != 0 || buf[i+1] != 0 {
			n++
		}
	}
	return n
}

func TestClipsLoadAndRender(t *testing.T) {
	names := ClipNames()
	if len(names) < 30 {
		t.Fatalf("clips %v", names)
	}
	for _, n := range names {
		if ClipLen(n) <= 0 {
			t.Fatalf("%s len", n)
		}
		for _, at := range []time.Duration{0, ClipLen(n) / 2, ClipLen(n)} {
			buf, ok := Clip(n, at, false)
			if !ok || len(buf) != Bytes {
				t.Fatalf("%s at %v", n, at)
			}
		}
	}
	if _, ok := Clip("nope", 0, false); ok {
		t.Fatal("unknown clip rendered")
	}
}

func TestBlissSquintsEyes(t *testing.T) {
	open, _ := Clip("petting_lvl1", 0, false)
	bliss, _ := Clip("petting_bliss", time.Second, true)
	if lit(bliss) >= lit(open)*3/4 || lit(bliss) == 0 {
		t.Fatalf("bliss lit %d vs open %d", lit(bliss), lit(open))
	}
}
