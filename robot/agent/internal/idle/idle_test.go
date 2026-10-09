package idle

import (
	"testing"
	"time"
)

func TestGlancesAndHeadStayInRange(t *testing.T) {
	l := New(1)
	t0 := time.Unix(1000, 0)
	head := int32(40)
	glances, moves := 0, 0
	for i := 0; i < 20*60*5; i++ { // 5 minutes at 20 Hz
		now := t0.Add(time.Duration(i) * 50 * time.Millisecond)
		o := l.Tick(In{Now: now, Head: head, HaveHead: true})
		switch o.Event {
		case "glance":
			glances++
		case "head":
			moves++
		}
		if o.Head > 0 {
			head += 4
		} else if o.Head < 0 {
			head -= 4
		}
		if head > HeadTop+headTol+4 || head < HeadBottom-headTol-4 {
			t.Fatalf("head out of range %d", head)
		}
		if o.LookX < -1 || o.LookX > 1 {
			t.Fatal("gaze")
		}
	}
	if glances < 8 || moves < 5 || glances+moves > 60 {
		t.Fatalf("glances=%d moves=%d", glances, moves)
	}
}

func TestStuckHeadTimesOut(t *testing.T) {
	l := New(2)
	t0 := time.Unix(0, 0)
	l.startHead(t0, -100)
	n := 0
	for i := 0; i < 40; i++ {
		if l.Tick(In{Now: t0.Add(time.Duration(i) * 50 * time.Millisecond), Head: 40, HaveHead: true}).Head != 0 {
			n++
		}
	}
	if n == 0 || n > 15 {
		t.Fatalf("drove %d ticks", n)
	}
}

func TestSoundGlanceAndCooldown(t *testing.T) {
	l := New(3)
	t0 := time.Unix(0, 0)
	o := l.Tick(In{Now: t0, Head: -100, HaveHead: true, Sound: true, SoundDir: 3})
	if o.Event != "sound" || o.LookX >= 0 || o.Head <= 0 {
		t.Fatalf("%+v", o)
	}
	if l.Tick(In{Now: t0.Add(time.Second), Head: -90, HaveHead: true, Sound: true, SoundDir: 9}).Event == "sound" {
		t.Fatal("cooldown")
	}
	if DirToGaze(9) <= 0 || DirToGaze(0) != 0 {
		t.Fatal("dir map")
	}
}

func TestPauseDropsMove(t *testing.T) {
	l := New(4)
	t0 := time.Unix(0, 0)
	l.Tick(In{Now: t0, Head: -100, HaveHead: true, Sound: true})
	l.Pause(t0.Add(50 * time.Millisecond))
	o := l.Tick(In{Now: t0.Add(100 * time.Millisecond), Head: -100, HaveHead: true})
	if o.Head != 0 || o.LookX != 0 {
		t.Fatalf("%+v", o)
	}
}

func TestSoundDetector(t *testing.T) {
	var s SoundDetector
	for i := 0; i < 100; i++ {
		if s.Feed(1000+int64(i%7)*50, false) {
			t.Fatal("noise fired")
		}
	}
	if !s.Feed(20000, false) {
		t.Fatal("clap missed")
	}
	if s.Feed(20000, true) {
		t.Fatal("own voice")
	}
}
