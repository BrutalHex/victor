package spine

import (
	"compress/gzip"
	"io"
	"math/rand"
	"os"
	"testing"
)

// Recorded idle frames (robot on the charger, 2026-10-09): VL53 range status
// 0x5B/0x5A -> 11 (valid), range 79-80 mm, signal ~21-28 MCPS.
func TestRecordedProx(t *testing.T) {
	for i, b := range recorded(t) {
		f, _ := ParsePacked(b)
		if !f.ProxValid || f.ProxStatus != ProxStatusValid || f.ProxMM < 75 || f.ProxMM > 85 {
			t.Fatalf("frame %d prox %+v", i, f)
		}
		if f.ProxRawRangeMM != f.ProxMM || f.ProxSignal < 15 || f.ProxSignal > 40 || f.ProxSamples == 0 {
			t.Fatalf("frame %d raw=%d sig=%.1f samples=%d", i, f.ProxRawRangeMM, f.ProxSignal, f.ProxSamples)
		}
	}
}

func TestProxDecodeAndInvalid(t *testing.T) {
	b := append([]byte(nil), recorded(t)[0]...)
	b[proxOff] = 0x20                       // status 4: signal fail
	b[proxOff+2], b[proxOff+3] = 0x01, 0x2C // 300 mm big-endian
	f, _ := ParsePacked(b)
	if f.ProxValid || f.ProxMM != 300 || f.ProxRawRangeMM != 0 || f.ProxStatus != 4 {
		t.Fatalf("%+v", f)
	}
	b[proxOff] = 0x58
	f, _ = ParsePacked(b)
	if !f.ProxValid || f.ProxRawRangeMM != 300 {
		t.Fatalf("%+v", f)
	}
}

// The prox fix must not move the Button (CHARGE-LATCH) by a single bit:
// every prox byte value, on every recorded frame, gives the legacy button.
func TestProxBytesNeverChangeButton(t *testing.T) {
	r := rand.New(rand.NewSource(7))
	for _, rec := range recorded(t) {
		for off := 74; off < 92; off++ {
			for v := 0; v < 256; v += 17 {
				b := append([]byte(nil), rec...)
				b[off] = byte(v)
				b[off^1] = byte(r.Intn(256))
				f, err := ParsePacked(b)
				if err != nil || f.Button != legacyButton(b) {
					t.Fatalf("off %d v %d: button %v legacy %v", off, v, f.Button, legacyButton(b))
				}
			}
		}
	}
}

func loadFrames(t *testing.T, name string) [][]byte {
	t.Helper()
	f, err := os.Open("testdata/" + name)
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
	return out
}

// Live 10 Oct: on the open floor the sensor reported status 11 with 6-65 mm at
// ~0.5 MCPS; these must not count as obstacles. A real wall at ~165 mm must.
func TestWeakProxReturnIsNotValid(t *testing.T) {
	for i, b := range loadFrames(t, "floor-noprox.bin.gz") {
		f, err := ParsePacked(b)
		if err != nil {
			t.Fatal(err)
		}
		if f.ProxStatus != ProxStatusValid || f.ProxSignal > 1 {
			t.Fatalf("frame %d: fixture changed? status %d signal %.2f", i, f.ProxStatus, f.ProxSignal)
		}
		if f.ProxValid || f.ProxRawRangeMM != 0 {
			t.Fatalf("frame %d: noise %d mm at %.2f MCPS counted as valid", i, f.ProxMM, f.ProxSignal)
		}
		if f.Button != legacyButton(b) {
			t.Fatalf("frame %d: button changed", i)
		}
	}
	for i, b := range loadFrames(t, "desk-wall165.bin.gz") {
		f, _ := ParsePacked(b)
		if !f.ProxValid || f.ProxMM < 150 || f.ProxMM > 185 || f.Button != legacyButton(b) {
			t.Fatalf("frame %d: wall %d mm valid=%v signal %.2f", i, f.ProxMM, f.ProxValid, f.ProxSignal)
		}
	}
}
