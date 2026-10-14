package main

import (
	"fmt"

	"github.com/BrutalHex/victor/robot/agent/internal/face"
	"os"
	"strconv"
	"sync"
	"time"
)

// thinkStale is how long the thinking face survives without a refresh from
// the hub. The hub re-sends "thinking|" every ~4 s for the whole reply turn
// (STT, chat/web search, TTS), so a slow 30 s TTS stays covered, while a hub
// restart or a lost "idle|" can't leave the face (and the closed mic) stuck.
var thinkStale = func() time.Duration {
	if v, err := strconv.Atoi(os.Getenv("VICTOR_THINK_STALE_S")); err == nil && v > 0 {
		return time.Duration(v) * time.Second
	}
	return 15 * time.Second
}()

type uiState struct {
	mu         sync.Mutex
	mode       string
	caption    string
	until      time.Time
	thinkStart time.Time
	animStart  time.Time // anim / pet / sleep clip start
	thinkSeen  time.Time
	thinkFrame uint64        // face.FramesSent at think on
	frames     func() uint64 // tests
	muteUntil  time.Time
	gx, gy     float64 // idle-life eye gaze offset
	lastDrawn  string
	now        func() time.Time // tests
	log        func(string)     // tests
}

func (u *uiState) clock() time.Time {
	if u.now != nil {
		return u.now()
	}
	return time.Now()
}

func (u *uiState) logf(format string, a ...any) {
	line := fmt.Sprintf(format, a...)
	if u.log != nil {
		u.log(line)
		return
	}
	fmt.Println(line)
}

func (u *uiState) sent() uint64 {
	if u.frames != nil {
		return u.frames()
	}
	return face.FramesSent()
}

// thinkStats: panel frames written while thinking and the rate.
func (u *uiState) thinkStats(now time.Time) string {
	n := u.sent() - u.thinkFrame
	held := now.Sub(u.thinkStart).Seconds()
	fps := 0.0
	if held > 0 {
		fps = float64(n) / held
	}
	return fmt.Sprintf("held=%.1fs frames=%d fps=%.1f", held, n, fps)
}

func stamp(t time.Time) string { return t.UTC().Format("15:04:05.000Z") }

// expireLocked drops a thinking face the hub stopped refreshing.
func (u *uiState) expireLocked(now time.Time) {
	if u.mode == "thinking" && now.Sub(u.thinkSeen) > thinkStale {
		u.mode, u.caption, u.until, u.lastDrawn = "idle", "", time.Time{}, ""
		u.logf("think off %s why=stale %s", stamp(now), u.thinkStats(now))
	}
}

func (u *uiState) thinking() bool {
	if u == nil {
		return false
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	u.expireLocked(u.clock())
	return u.mode == "thinking"
}

// speakStart ends thinking when playback begins; other faces (a name card
// shown while greeting) stay.
func (u *uiState) speakStart() {
	if u == nil {
		return
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.mode == "thinking" {
		now := u.clock()
		u.mode, u.caption, u.until, u.lastDrawn = "idle", "", time.Time{}, ""
		u.logf("think off %s why=speak %s", stamp(now), u.thinkStats(now))
	}
}

// muteFor keeps the mic stream closed while the speaker plays.
func (u *uiState) muteFor(d time.Duration) {
	if u == nil {
		return
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	u.muteUntil = u.clock().Add(d)
}

func (u *uiState) muted() bool {
	if u == nil {
		return false
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.clock().Before(u.muteUntil)
}

func (u *uiState) set(mode, caption string, d time.Duration) {
	if u == nil {
		return
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	now := u.clock()
	if mode == "thinking" {
		u.thinkSeen = now
		if u.mode == "thinking" {
			return // keepalive: keep the animation phase running
		}
		u.thinkStart = now
		u.thinkFrame = u.sent()
		u.logf("think on %s", stamp(now))
	} else if u.mode == "thinking" {
		u.logf("think off %s why=%s %s", stamp(now), mode, u.thinkStats(now))
	}
	if mode != u.mode || caption != u.caption {
		u.animStart = now
	}
	u.mode, u.caption = mode, caption
	if d > 0 {
		u.until = now.Add(d)
	} else {
		u.until = time.Time{}
	}
	u.lastDrawn = ""
}

func (u *uiState) currentMode() string {
	if u == nil {
		return ""
	}
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.mode
}

func (u *uiState) setGaze(x, y float64) {
	if u == nil {
		return
	}
	u.mu.Lock()
	u.gx, u.gy = x, y
	u.mu.Unlock()
}
