package statusfile

import (
	"testing"
	"time"
)

func TestRateLimited(t *testing.T) {
	now := time.Unix(0, 0)
	n := 0
	w := New()
	w.now = func() time.Time { return now }
	w.write = func(string, []byte) error { n++; return nil }
	// 50 Hz for 10 s, value changing every tick: ≤ 2 writes/s
	for i := 0; i < 500; i++ {
		w.Put("/x", string(rune('a'+i%20)))
		now = now.Add(20 * time.Millisecond)
	}
	if n > 21 {
		t.Fatalf("%d writes for a changing value", n)
	}
	// unchanged value: refreshed every Max only
	n = 0
	for i := 0; i < 500; i++ {
		w.Put("/y", "same")
		now = now.Add(20 * time.Millisecond)
	}
	if n != 2 {
		t.Fatalf("%d writes for a constant value", n)
	}
}
