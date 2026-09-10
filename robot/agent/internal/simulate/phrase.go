package simulate

import (
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/latch"
)

// ChargeLatch feeds a valid CHARGE-LATCH phrase into the machine.
func ChargeLatch(m *latch.Machine) latch.Result {
	now := time.Duration(0)
	feed := func(dt time.Duration, button bool, lift float64) latch.Result {
		now += dt
		return m.Feed(latch.Sample{
			T:         now,
			Button:    button,
			OnCharger: true,
			LiftNorm:  lift,
		})
	}
	clk := func() {
		feed(50*time.Millisecond, true, 0)
		feed(50*time.Millisecond, false, 0)
	}
	clk()
	clk()
	feed(latch.MultiClickGap, false, 0)
	feed(80*time.Millisecond, false, 0.95)
	feed(latch.LiftHold, false, 0.95)
	feed(80*time.Millisecond, false, 0.05)
	clk = func() {
		feed(50*time.Millisecond, true, 0.05)
		feed(50*time.Millisecond, false, 0.05)
	}
	clk()
	clk()
	clk()
	return feed(latch.MultiClickGap, false, 0.05)
}
