// Package latch implements CHARGE-LATCH: on-charger backpack + lift phrase
// that toggles SSH. See GROK_INSTRUCTIONS.md.
package latch

import "time"

const (
	ClickMin           = 40 * time.Millisecond
	ClickMax           = 250 * time.Millisecond
	MultiClickGap      = 400 * time.Millisecond
	AfterDoubleLift    = 2 * time.Second
	LiftHold           = 400 * time.Millisecond
	AfterBottomTriple  = 2 * time.Second
	PhraseMax          = 6 * time.Second
	LiftTop            = 0.85
	LiftBottom         = 0.15
	WheelDriveTicks    = 40
)

type Phase int

const (
	Idle Phase = iota
	AwaitLiftTop
	HoldingTop
	AwaitLiftBottom
	AwaitTriple
)

func (p Phase) String() string {
	switch p {
	case Idle:
		return "idle"
	case AwaitLiftTop:
		return "await_lift_top"
	case HoldingTop:
		return "holding_top"
	case AwaitLiftBottom:
		return "await_lift_bottom"
	case AwaitTriple:
		return "await_triple"
	default:
		return "unknown"
	}
}

type Sample struct {
	T          time.Duration
	Button     bool
	OnCharger  bool
	Driving    bool
	LiftNorm   float64 // 0 = bottom, 1 = top
}

type Result int

const (
	None Result = iota
	ToggleSSH
)

type Machine struct {
	phase        Phase
	prevButton   bool
	downAt       time.Duration
	held         bool
	clicks       []time.Duration
	groupOpen    bool
	phraseStart  time.Duration
	doubleDoneAt time.Duration
	topSince     time.Duration
	bottomAt     time.Duration
}

func New() *Machine {
	return &Machine{}
}

func (m *Machine) Phase() Phase { return m.phase }

func (m *Machine) Reset() {
	m.phase = Idle
	m.clicks = m.clicks[:0]
	m.groupOpen = false
	m.held = false
	m.phraseStart = 0
	m.doubleDoneAt = 0
	m.topSince = 0
	m.bottomAt = 0
}

func (m *Machine) Feed(s Sample) Result {
	if s.Driving || !s.OnCharger {
		m.Reset()
		m.prevButton = s.Button
		return None
	}

	nClicks := m.ingestButton(s)
	if m.phase != Idle && m.phraseStart > 0 && s.T-m.phraseStart > PhraseMax {
		m.Reset()
		m.prevButton = s.Button
		return None
	}

	switch m.phase {
	case Idle:
		if nClicks == 2 {
			m.phase = AwaitLiftTop
			m.doubleDoneAt = s.T
			if m.phraseStart == 0 {
				m.phraseStart = s.T
			}
		}
	case AwaitLiftTop:
		if s.T-m.doubleDoneAt > AfterDoubleLift {
			m.Reset()
			break
		}
		if s.LiftNorm >= LiftTop {
			m.phase = HoldingTop
			m.topSince = s.T
		}
	case HoldingTop:
		if s.LiftNorm < LiftTop {
			m.phase = AwaitLiftTop
			if s.T-m.doubleDoneAt > AfterDoubleLift {
				m.Reset()
			}
			break
		}
		if s.T-m.topSince >= LiftHold {
			m.phase = AwaitLiftBottom
		}
	case AwaitLiftBottom:
		if s.LiftNorm <= LiftBottom {
			m.phase = AwaitTriple
			m.bottomAt = s.T
			m.clicks = m.clicks[:0]
			m.groupOpen = false
		}
	case AwaitTriple:
		if s.T-m.bottomAt > AfterBottomTriple {
			m.Reset()
			break
		}
		if nClicks == 3 {
			m.Reset()
			m.prevButton = s.Button
			return ToggleSSH
		}
	}
	m.prevButton = s.Button
	return None
}

func (m *Machine) ingestButton(s Sample) int {
	completed := 0
	if s.Button && !m.prevButton {
		m.downAt = s.T
		m.held = true
	}
	if !s.Button && m.prevButton && m.held {
		m.held = false
		dt := s.T - m.downAt
		if dt >= ClickMin && dt <= ClickMax {
			if m.groupOpen && len(m.clicks) > 0 && s.T-m.clicks[len(m.clicks)-1] > MultiClickGap {
				m.clicks = m.clicks[:0]
			}
			if !m.groupOpen {
				m.clicks = m.clicks[:0]
				m.groupOpen = true
				if m.phase == Idle {
					m.phraseStart = s.T
				}
			}
			m.clicks = append(m.clicks, s.T)
		}
	}
	if m.groupOpen && len(m.clicks) > 0 && !m.held && s.T-m.clicks[len(m.clicks)-1] >= MultiClickGap {
		completed = len(m.clicks)
		m.groupOpen = false
		m.clicks = m.clicks[:0]
	}
	return completed
}
