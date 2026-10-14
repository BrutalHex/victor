package rotlog

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func total(t *testing.T, p string) int64 {
	var n int64
	for _, f := range []string{p, p + ".1"} {
		if st, err := os.Stat(f); err == nil {
			n += st.Size()
		}
	}
	return n
}

func TestCapNeverExceeded(t *testing.T) {
	p := filepath.Join(t.TempDir(), "telemetry.log")
	l := New(p)
	l.Max = 64 << 10
	l.Flush = 0
	line := strings.Repeat("x", 99) + "\n"
	for i := 0; i < 5000; i++ { // 500 KB written into a 64 KB cap
		if err := l.WriteLine(line); err != nil {
			t.Fatal(err)
		}
		if n := total(t, p); n > l.Max {
			t.Fatalf("after %d lines total %d > %d", i, n, l.Max)
		}
	}
	l.Close()
	if total(t, p) < l.Max/2 {
		t.Fatal("rotation lost too much history")
	}
}

func TestOversizedLeftoverShrunkKeepsTail(t *testing.T) {
	p := filepath.Join(t.TempDir(), "telemetry.log")
	var b strings.Builder
	for i := 0; i < 20000; i++ {
		b.WriteString("line ")
		b.WriteString(strings.Repeat("y", 40))
		b.WriteString("\n")
	}
	b.WriteString("LAST\n")
	if err := os.WriteFile(p, []byte(b.String()), 0644); err != nil {
		t.Fatal(err)
	}
	l := New(p)
	l.Max = 100 << 10
	if err := l.WriteLine("new\n"); err != nil {
		t.Fatal(err)
	}
	l.Close()
	if n := total(t, p); n > l.Max {
		t.Fatalf("total %d", n)
	}
	old, _ := os.ReadFile(p + ".1")
	if !strings.HasSuffix(string(old), "LAST\n") || !strings.HasPrefix(string(old), "line ") {
		t.Fatal("tail not kept on a line boundary")
	}
	cur, _ := os.ReadFile(p)
	if string(cur) != "new\n" {
		t.Fatalf("cur %q", cur)
	}
}

func TestBufferedFlush(t *testing.T) {
	p := filepath.Join(t.TempDir(), "telemetry.log")
	now := time.Unix(100, 0)
	l := New(p)
	l.now = func() time.Time { return now }
	_ = l.WriteLine("a\n") // first write flushes (flushed is zero)
	_ = l.WriteLine("b\n")
	if b, _ := os.ReadFile(p); string(b) != "a\n" {
		t.Fatalf("buffered %q", b)
	}
	now = now.Add(3 * time.Second)
	_ = l.WriteLine("c\n")
	if b, _ := os.ReadFile(p); string(b) != "a\nb\nc\n" {
		t.Fatalf("flushed %q", b)
	}
}
