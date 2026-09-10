package cliffcal

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCalibratesFromFloor(t *testing.T) {
	c := &Cal{}
	for i := 0; i < need; i++ {
		c.Add([4]uint16{400, 420, 390, 410})
	}
	if !c.Ready() {
		t.Fatal("should be ready")
	}
	if c.Thresh[0] < 140 || c.Thresh[0] > 180 {
		t.Fatalf("thresh %v", c.Thresh)
	}
}

func TestRejectsDarkCal(t *testing.T) {
	c := &Cal{}
	for i := 0; i < need; i++ {
		c.Add([4]uint16{10, 10, 10, 10})
	}
	if c.Ready() {
		t.Fatal("void readings must not calibrate")
	}
}

func TestRoundTrip(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "cliffs.cal")
	c := &Cal{}
	for i := 0; i < need; i++ {
		c.Add([4]uint16{500, 500, 500, 500})
	}
	if err := c.Save(p); err != nil {
		t.Fatal(err)
	}
	got, err := Load(p)
	if err != nil {
		t.Fatal(err)
	}
	if got.Thresh[0] != c.Thresh[0] {
		t.Fatalf("%v vs %v", got.Thresh, c.Thresh)
	}
	_ = os.Remove(p)
}
