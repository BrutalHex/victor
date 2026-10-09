package main

import (
	"fmt"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/audio"
	"github.com/BrutalHex/victor/robot/agent/internal/face"
	"github.com/BrutalHex/victor/robot/agent/internal/spine"
	"github.com/BrutalHex/victor/robot/agent/internal/statusfile"
	"github.com/BrutalHex/victor/robot/agent/internal/touch"
)

// petting is the stock-like back-touch reaction: happy squint eyes that grow
// into bliss the longer the back is stroked (DDL anim_petting_lvl1..4 and
// blissloop face tracks), a soft purr when the speaker is free, and the
// bliss getout when the hand leaves. It never touches motors, LEDs or the
// latch, and it does not take over the face while a reply turn is running.
type petting struct {
	ui        *uiState
	d         touch.Detector
	lastPurr  time.Time
	purrUntil time.Time
	lastLog   time.Time
	purr      []byte
	play      func([]byte) bool // tests
}

var petClips = []string{"petting_lvl1", "petting_lvl1", "petting_lvl2", "petting_lvl3", "petting_lvl4", "petting_bliss"}

func (p *petting) feed(now time.Time, fr spine.Frame, status *statusfile.Writer) {
	ev := p.d.Feed(now, fr.Touch, fr.OnCharger())
	if status != nil && now.Sub(p.lastLog) >= 500*time.Millisecond {
		p.lastLog = now
		status.Put("/data/victor/touch.txt", fmt.Sprintf("raw=%d level=%d hires=%d filt=%.0f base=%.0f cal=%v touching=%v petting=%v lvl=%d strokes=%d\n",
			fr.Touch, fr.TouchLevel, fr.TouchHires, p.d.Filtered, p.d.Baseline(), p.d.Calibrated(), p.d.Touching(), p.d.Petting(), p.d.Level(), p.d.Strokes()))
	}
	if ev == touch.None {
		return
	}
	fmt.Printf("touch %s raw=%d base=%.0f lvl=%d strokes=%d\n", ev, fr.Touch, p.d.Baseline(), p.d.Level(), p.d.Strokes())
	if ev == touch.Released {
		if p.ui != nil && p.ui.currentMode() == "pet" {
			p.ui.set("anim", "petting_getout", face.ClipLen("petting_getout"))
		}
		return
	}
	if p.busy(now) {
		return
	}
	switch ev {
	case touch.PetStart, touch.PetLevel:
		lvl := p.d.Level()
		if lvl >= len(petClips) {
			lvl = len(petClips) - 1
		}
		p.ui.set("pet", petClips[lvl], 0)
		p.maybePurr(now)
	}
}

// busy: a reply turn (thinking / speaking) or an action face owns the screen.
func (p *petting) busy(now time.Time) bool {
	if p.ui == nil {
		return true
	}
	if p.ui.thinking() || (p.ui.muted() && now.After(p.purrUntil)) {
		return true // muted by speech, not by our own purr
	}
	p.ui.mu.Lock()
	defer p.ui.mu.Unlock()
	return p.ui.mode == "anim" && p.ui.caption != "petting_getout"
}

func (p *petting) maybePurr(now time.Time) {
	if now.Sub(p.lastPurr) < 2500*time.Millisecond {
		return
	}
	p.lastPurr = now
	if p.purr == nil {
		p.purr = audio.Purr(1.6)
	}
	play := p.play
	if play == nil {
		play = audio.PlaySoft
	}
	pcm := p.purr
	// mic closed while purring so the hub never hears it as speech
	p.ui.muteFor(2 * time.Second)
	p.purrUntil = now.Add(2 * time.Second)
	go play(pcm)
}

func (p *petting) touching() bool { return p != nil && p.d.Touching() }
