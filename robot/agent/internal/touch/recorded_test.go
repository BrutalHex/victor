package touch

import (
	"compress/gzip"
	"io"
	"os"
	"testing"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/spine"
)

// Frames recorded on the robot (idle, on the charger, 2026-10-09) never
// produce a touch, replayed for a minute.
func TestRecordedIdleFramesNoTouch(t *testing.T) {
	f, err := os.Open("../spine/testdata/idle-oncharger.bin.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	zr, _ := gzip.NewReader(f)
	raw, _ := io.ReadAll(zr)
	var d Detector
	now := time.Unix(0, 0)
	for rep := 0; rep < 40; rep++ {
		for i := 0; i+spine.DataRXSize <= len(raw); i += spine.DataRXSize {
			fr, err := spine.ParsePacked(raw[i : i+spine.DataRXSize])
			if err != nil {
				t.Fatal(err)
			}
			if e := d.Feed(now, fr.Touch, fr.OnCharger()); e != None {
				t.Fatalf("event %s at %d", e, i)
			}
			now = now.Add(20 * time.Millisecond)
		}
	}
	if !d.Calibrated() || d.Baseline() < 5400 || d.Baseline() > 5500 {
		t.Fatalf("baseline %.0f", d.Baseline())
	}
}
