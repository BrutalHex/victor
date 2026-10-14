package spine

import (
	"bytes"
	"encoding/binary"
	"io"
	"syscall"
	"testing"
	"time"
)

func TestDrainReadsKeepsGoingUntilEmpty(t *testing.T) {
	buf := make([]byte, 4)
	calls := 0
	var got []byte
	err := drainReads(func(p []byte) (int, error) {
		calls++
		if calls > 2 {
			return 0, syscall.EAGAIN
		}
		for i := range p {
			p[i] = byte(calls)
		}
		return len(p), nil
	}, buf, func(p []byte) {
		got = append(got, p...)
	})
	if err != nil {
		t.Fatal(err)
	}
	if calls != 3 || len(got) != 8 || got[0] != 1 || got[4] != 2 {
		t.Fatalf("calls %d got %v", calls, got)
	}
}

func TestAppendRXMicBlock(t *testing.T) {
	payload := make([]byte, 768)
	for i := 0; i < 320; i++ {
		binary.LittleEndian.PutUint16(payload[128+i*2:], uint16(1000+i))
	}
	raw := Encode(TypeData, payload)
	binary.LittleEndian.PutUint32(raw[0:4], HeaderRX)
	var b Body
	b.appendRX(raw)
	mic := b.DrainMic()
	if len(mic) != 320 || mic[0] != 1000 || mic[319] != 1319 {
		t.Fatalf("mic len %d", len(mic))
	}
	if _, ok := b.Last(); !ok {
		t.Fatal("frame not stored")
	}
	if b.lastFrame.IsZero() {
		t.Fatal("last frame time")
	}
}

func TestRxNeedsRecover(t *testing.T) {
	now := time.Unix(1000, 0)
	stale := 400 * time.Millisecond
	gap := time.Second
	if rxNeedsRecover(now, time.Time{}, now, stale, gap) {
		t.Fatal("fresh stream")
	}
	if !rxNeedsRecover(now.Add(-time.Second), time.Time{}, now, stale, gap) {
		t.Fatal("stale stream")
	}
	if rxNeedsRecover(now.Add(-time.Second), now.Add(-200*time.Millisecond), now, stale, gap) {
		t.Fatal("inside recover gap")
	}
	if !rxNeedsRecover(now.Add(-2*time.Second), now.Add(-2*time.Second), now, stale, gap) {
		t.Fatal("gap elapsed")
	}
}

type shortWriter struct {
	bytes.Buffer
	n int
}

func (s *shortWriter) Write(p []byte) (int, error) {
	if len(p) > s.n {
		p = p[:s.n]
	}
	return s.Buffer.Write(p)
}

func TestWriteFullShortWrites(t *testing.T) {
	w := &shortWriter{n: 3}
	payload := bytes.Repeat([]byte{0xAB}, 20)
	if err := writeFull(w, payload); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(w.Bytes(), payload) {
		t.Fatalf("wrote %d", w.Len())
	}
}

func TestWriteFullZeroWrite(t *testing.T) {
	err := writeFull(zeroWriter{}, []byte{1})
	if err != io.ErrShortWrite {
		t.Fatalf("%v", err)
	}
}

type zeroWriter struct{}

func (zeroWriter) Write([]byte) (int, error) { return 0, nil }
