package simulate

import (
	"testing"

	"github.com/BrutalHex/victor/robot/agent/internal/latch"
)

func TestChargeLatch(t *testing.T) {
	m := latch.New()
	if ChargeLatch(m) != latch.ToggleSSH {
		t.Fatalf("phase=%s", m.Phase())
	}
}
