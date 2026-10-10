package spine

import (
	"compress/gzip"
	"encoding/binary"
	"io"
	"math/rand"
	"os"
	"testing"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/latch"
)

// legacyButton is the exact expression ParsePacked used before the touch fix
// (commit 4c0f768): touchOff = 56+8+10+1+14 = 89, button = u16 at 91 > 0.
func legacyButton(b []byte) bool {
	return binary.LittleEndian.Uint16(b[89+2:]) > 0
}

func recorded(t *testing.T) [][]byte {
	t.Helper()
	f, err := os.Open("testdata/idle-oncharger.bin.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zr, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(zr)
	var out [][]byte
	for i := 0; i+DataRXSize <= len(raw); i += DataRXSize {
		out = append(out, raw[i:i+DataRXSize])
	}
	if len(out) != 80 {
		t.Fatalf("frames %d", len(out))
	}
	return out
}

func TestRecordedTouchFields(t *testing.T) {
	for i, b := range recorded(t) {
		f, err := ParsePacked(b)
		if err != nil {
			t.Fatal(err)
		}
		// Idle robot on the charger, 2026-10-09: touchLevel ~682, touchHires ~5461.
		if f.TouchLevel < 670 || f.TouchLevel > 700 || f.TouchHires < 5400 || f.TouchHires > 5500 {
			t.Fatalf("frame %d level=%d hires=%d", i, f.TouchLevel, f.TouchHires)
		}
		if f.Touch != f.TouchHires {
			t.Fatalf("touch %d hires %d", f.Touch, f.TouchHires)
		}
		if !f.OnCharger() || f.BattVoltage < 2600 {
			t.Fatalf("frame %d sanity %+v", i, f)
		}
	}
}

func TestButtonBitIdenticalToLegacy(t *testing.T) {
	check := func(b []byte, what string) {
		f, err := ParsePacked(b)
		if err != nil {
			t.Fatal(err)
		}
		if f.Button != legacyButton(b) {
			t.Fatalf("%s: button %v legacy %v", what, f.Button, legacyButton(b))
		}
	}
	for i, b := range recorded(t) {
		check(b, "recorded")
		// the same frames with every value of the two button bytes
		c := append([]byte(nil), b...)
		for v := 0; v < 65536; v += 257 {
			binary.LittleEndian.PutUint16(c[ButtonBytes:], uint16(v))
			check(c, "recorded-sweep")
		}
		_ = i
	}
	r := rand.New(rand.NewSource(1))
	for i := 0; i < 20000; i++ {
		b := make([]byte, DataRXSize)
		r.Read(b)
		check(b, "random")
	}
}

// Petting changes the touch bytes. Whatever the touch level does, with the
// lift still the CHARGE-LATCH can never toggle (it needs the lift phrase).
func TestTouchNeverTogglesLatch(t *testing.T) {
	base := recorded(t)
	m := latch.New()
	tick := 0
	feed := func(level, hires uint16) {
		b := append([]byte(nil), base[tick%len(base)]...)
		binary.LittleEndian.PutUint16(b[touchLevelOff:], level)
		binary.LittleEndian.PutUint16(b[touchHiresOff:], hires)
		f, _ := ParsePacked(b)
		if m.Feed(latch.Sample{T: latchT(tick), Button: f.Button, OnCharger: f.OnCharger(), LiftNorm: 0}) == latch.ToggleSSH {
			t.Fatalf("touch toggled the latch at tick %d level=%d", tick, level)
		}
		tick++
	}
	r := rand.New(rand.NewSource(7))
	for rep := 0; rep < 200; rep++ {
		// strokes sweeping through every low byte (incl. 0x00 = legacy "release")
		for v := 600; v < 1100; v += 1 + r.Intn(3) {
			feed(uint16(v), uint16(5400+(v-600)*4))
		}
		// holds at multiples of 256 (low byte 0) for 40-250 ms, i.e. "clicks"
		for k := 0; k < 5; k++ {
			hold := 8 + r.Intn(40)
			for j := 0; j < hold; j++ {
				feed(768, 6000)
			}
			for j := 0; j < 10; j++ {
				feed(770, 6010)
			}
		}
	}
}

func latchT(tick int) time.Duration { return time.Duration(tick) * 5 * time.Millisecond }
