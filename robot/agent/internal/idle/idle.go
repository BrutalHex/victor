// Package idle is Vector's "alive" layer while nothing else is going on:
// eye glances, small head fidgets and a glance (eyes + head up) toward a
// sudden sound. No wheels and no lift, ever: the lift feeds the SSH
// charge-latch gesture, and wheels stay behind explore.enabled. The caller
// only ticks it when the veto is clear, no action/turn/petting is running
// and the face is idle.
package idle

import (
	"math"
	"math/rand"
	"os"
	"time"
)

// DisableFlag turns idle life off (var for tests).
var DisableFlag = "/data/victor/idle-life.disabled"

// Head encoder limits seen on this robot (top ~48, bottom ~-250) with margins.
var (
	HeadTop    int32 = 40
	HeadBottom int32 = -200
)

const (
	headPower   = int16(9830) // 0.3 power
	headTol     = 8
	moveTimeout = 700 * time.Millisecond
	minGap      = 5 * time.Second
	maxGap      = 12 * time.Second
	soundHold   = 2500 * time.Millisecond
	soundCool   = 4 * time.Second
)

type In struct {
	Now      time.Time
	Head     int32 // motors[3] position
	HaveHead bool
	Sound    bool // a sudden sound this tick
	SoundDir int  // 0 = front, clockwise in 30 degree steps (audio.Processor.Direction)
}

type Out struct {
	Head  int16   // head PWM, 0 = leave it
	LookX float64 // eye gaze offset, -1..1 (viewer's left..right)
	LookY float64
	Event string // non-empty once when something starts (for the log)
}

type Life struct {
	rng       *rand.Rand
	next      time.Time
	target    int32
	moveUntil time.Time
	moving    bool
	gx, gy    float64
	gazeUntil time.Time
	lastSound time.Time
	paused    bool
}

func New(seed int64) *Life { return &Life{rng: rand.New(rand.NewSource(seed))} }

func Disabled() bool {
	_, err := os.Stat(DisableFlag)
	return err == nil
}

// Pause is called on ticks where idle life must not run; it drops any
// move/glance and waits a little before the next one.
func (l *Life) Pause(now time.Time) {
	l.moving, l.gx, l.gy, l.gazeUntil = false, 0, 0, time.Time{}
	if !l.paused || l.next.Before(now.Add(3*time.Second)) {
		l.next = now.Add(3 * time.Second)
	}
	l.paused = true
}

func (l *Life) gap() time.Duration {
	return minGap + time.Duration(l.rng.Int63n(int64(maxGap-minGap)))
}

func (l *Life) startHead(now time.Time, target int32) {
	if target > HeadTop {
		target = HeadTop
	}
	if target < HeadBottom {
		target = HeadBottom
	}
	l.target, l.moveUntil, l.moving = target, now.Add(moveTimeout), true
}

// DirToGaze maps a mic direction to an eye offset. Sound from the robot's
// right (clockwise 1..5) is on the viewer's left of the screen.
func DirToGaze(dir int) float64 {
	dir = ((dir % 12) + 12) % 12
	if dir == 0 || dir == 6 {
		return 0
	}
	a := float64(dir) * math.Pi / 6
	return -0.7 * math.Sin(a)
}

func (l *Life) Tick(in In) Out {
	var out Out
	now := in.Now
	if l.paused || l.next.IsZero() {
		l.paused = false
		if l.next.IsZero() {
			l.next = now.Add(l.gap())
		}
	}
	if in.Sound && now.Sub(l.lastSound) >= soundCool {
		l.lastSound = now
		l.gx, l.gy, l.gazeUntil = DirToGaze(in.SoundDir), -0.25, now.Add(soundHold)
		if in.HaveHead && in.Head < HeadTop-30 {
			l.startHead(now, in.Head+60) // look up toward people
		}
		l.next = now.Add(l.gap())
		out.Event = "sound"
	} else if !now.Before(l.next) {
		l.next = now.Add(l.gap())
		switch r := l.rng.Float64(); {
		case r < 0.55 || !in.HaveHead:
			l.gx = (l.rng.Float64()*2 - 1) * 0.55
			l.gy = (l.rng.Float64()*2 - 1) * 0.3
			l.gazeUntil = now.Add(time.Second + time.Duration(l.rng.Int63n(int64(1500*time.Millisecond))))
			out.Event = "glance"
		default:
			d := int32(25 + l.rng.Intn(55))
			if l.rng.Intn(2) == 0 {
				d = -d
			}
			t := in.Head + d
			if t > HeadTop || t < HeadBottom {
				t = in.Head - d
			}
			l.startHead(now, t)
			// glance the same way the head goes
			l.gx, l.gy, l.gazeUntil = (l.rng.Float64()*2-1)*0.3, -0.2*float64(d)/80, now.Add(1500*time.Millisecond)
			out.Event = "head"
		}
	}
	if now.Before(l.gazeUntil) {
		out.LookX, out.LookY = l.gx, l.gy
	}
	if l.moving {
		switch {
		case !in.HaveHead || now.After(l.moveUntil):
			l.moving = false
		case in.Head < l.target-headTol:
			out.Head = headPower
		case in.Head > l.target+headTol:
			out.Head = -headPower
		default:
			l.moving = false
		}
	}
	return out
}

// SoundDetector flags a sudden loud sound over a slow noise floor.
type SoundDetector struct {
	floor float64
	n     int
}

// Feed takes the loudest mic channel's energy for one block; muted = the
// robot itself is talking (never react to our own voice).
func (s *SoundDetector) Feed(energy int64, muted bool) bool {
	e := float64(energy)
	if e <= 0 {
		return false
	}
	if s.n < 20 {
		s.n++
		if s.floor == 0 || e < s.floor {
			s.floor = e
		} else {
			s.floor += (e - s.floor) * 0.1
		}
		return false
	}
	hit := !muted && e > 8*s.floor
	if !hit {
		s.floor += (e - s.floor) * 0.02
	}
	return hit
}
