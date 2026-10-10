package action

import (
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/BrutalHex/victor/robot/agent/internal/drivectl"
)

type sim struct {
	r     Runner
	now   time.Time
	l, rr int32
	lift  int32
	outs  []Out
	maxW  int16
	done  string
	clips []string
	// brk > 0: tread static friction (live floor 10 Oct): a wheel starts at
	// |power| >= brk and keeps rolling while |power| > 0.15.
	brk        float64
	movL, movR bool
	prox       uint16 // > 0: fixed valid ToF reading
	maxV       float64
}

func (s *sim) wheel(pwm int16, moving *bool) int32 {
	if s.brk == 0 {
		return int32(float64(pwm) * 0.0003 * 20)
	}
	a := float64(pwm) / full
	if a < 0 {
		a = -a
	}
	if !*moving && a >= s.brk {
		*moving = true
	}
	if !*moving || a <= 0.15 {
		*moving = false
		return 0
	}
	v := (a - 0.15) * 160 / mmPerTick * 0.02 // ticks per 20 ms
	if pwm < 0 {
		v = -v
	}
	return int32(v)
}

func newSim(t *testing.T) *sim {
	DisableFlag = filepath.Join(t.TempDir(), "voice-drive.disabled")
	return &sim{now: time.Unix(0, 0)}
}

// run ticks at 20 ms; the wheels move 0.3 counts per tick per 1000 PWM.
func (s *sim) run(name string, onCharger bool, ticks int, abortAt int, bumpAt int) {
	s.r.Start(name, s.now)
	for i := 0; i < ticks; i++ {
		in := In{Now: s.now, OnCharger: onCharger, Abort: abortAt >= 0 && i >= abortAt, EncL: s.l, EncR: s.rr, EncLift: s.lift}
		in.ProxValid, in.ProxMM = s.prox > 0, s.prox
		o := s.r.Tick(in)
		s.outs = append(s.outs, o)
		if o.Clip != "" {
			s.clips = append(s.clips, o.Clip)
		}
		for _, w := range o.PWM[:2] {
			if w > s.maxW {
				s.maxW = w
			}
			if -w > s.maxW {
				s.maxW = -w
			}
		}
		dl := s.wheel(o.PWM[0], &s.movL)
		if v := math.Abs(float64(dl)) * mmPerTick / 0.02; v > s.maxV {
			s.maxV = v
		}
		s.l += dl
		s.rr += s.wheel(o.PWM[1], &s.movR)
		if bumpAt >= 0 && i == bumpAt {
			s.lift += 40
		}
		if o.Done != "" && s.done == "" {
			s.done = o.Done
		}
		s.now = s.now.Add(20 * time.Millisecond)
	}
}

func TestNoWheelsOnCharger(t *testing.T) {
	for _, n := range []string{"forward", "backup", "turn_left", "turn_around", "come_here", "dance"} {
		s := newSim(t)
		s.run(n, true, 400, -1, -1)
		if s.maxW != 0 {
			t.Fatalf("%s drove on the charger (pwm %d)", n, s.maxW)
		}
		if s.done == "" || s.r.Active() {
			t.Fatalf("%s did not finish: %q", n, s.done)
		}
	}
}

func TestLeaveChargerDrivesShortAndStops(t *testing.T) {
	s := newSim(t)
	s.run("leave_charger", true, 400, -1, -1)
	if s.maxW == 0 || s.maxW > 12452 {
		t.Fatalf("pwm %d", s.maxW)
	}
	mm := float64(s.l) * mmPerTick
	if mm < 85 || mm > 130 {
		t.Fatalf("drove %.0f mm", mm)
	}
	if s.done == "" || s.r.Active() {
		t.Fatalf("done %q", s.done)
	}
	last := s.outs[len(s.outs)-1]
	if last.PWM != [4]int16{} {
		t.Fatalf("motors left on %v", last.PWM)
	}
}

func TestVetoAbortsAtOnce(t *testing.T) {
	s := newSim(t)
	s.run("forward", false, 100, 10, -1)
	if s.done != "aborted: veto" {
		t.Fatalf("done %q", s.done)
	}
	for _, o := range s.outs[10:] {
		if o.PWM != [4]int16{} || o.Active {
			t.Fatalf("moving after veto %+v", o)
		}
	}
}

func TestDisableFlagHoldsWheels(t *testing.T) {
	s := newSim(t)
	_ = os.WriteFile(DisableFlag, []byte("1"), 0644)
	s.run("forward", false, 300, -1, -1)
	if s.maxW != 0 {
		t.Fatalf("pwm %d", s.maxW)
	}
}

func TestFistbumpOnChargerNoLift(t *testing.T) {
	s := newSim(t)
	s.run("fistbump", true, 600, -1, -1)
	for _, o := range s.outs {
		if o.PWM[2] != 0 {
			t.Fatalf("lift moved on charger %v", o.PWM)
		}
	}
	if s.clips[0] != "fistbump_request" {
		t.Fatalf("clips %v", s.clips)
	}
}

func TestFistbumpOffChargerFeelsBump(t *testing.T) {
	s := newSim(t)
	s.run("fistbump", false, 600, -1, 80)
	if !s.r.Bumped() {
		t.Fatalf("no bump: %q %v", s.done, s.clips)
	}
	found := false
	for _, c := range s.clips {
		found = found || c == "fistbump_success"
	}
	if !found {
		t.Fatalf("clips %v", s.clips)
	}
	s2 := newSim(t)
	s2.run("fistbump", false, 600, -1, -1)
	if s2.r.Bumped() || s2.clips[len(s2.clips)-1] != "fistbump_fail" {
		t.Fatalf("clips %v", s2.clips)
	}
}

func TestStalledMoveTimesOut(t *testing.T) {
	s := newSim(t)
	s.r.Start("forward", s.now)
	var o Out
	for i := 0; i < 400 && (i == 0 || o.Active); i++ {
		o = s.r.Tick(In{Now: s.now}) // encoders never move
		if float64(o.PWM[0]) > drivectl.KickMax*full+1 {
			t.Fatalf("power above cap %d", o.PWM[0])
		}
		s.now = s.now.Add(20 * time.Millisecond)
	}
	if s.r.Active() {
		t.Fatal("still active")
	}
}

func TestUnknownPlanIgnored(t *testing.T) {
	var r Runner
	if r.Start("self_destruct", time.Now()) || r.Active() {
		t.Fatal("unknown plan started")
	}
}

// Live 10 Oct: the old fixed creep power (<= 0.38) never broke the treads
// loose on the floor. Voice moves share drivectl and must complete.
func TestVoiceMovesBreakAway(t *testing.T) {
	for _, c := range []struct {
		name   string
		wantMM float64 // forward distance (drive) or arc per wheel (turn)
	}{{"forward", 60}, {"backup", -50}, {"turn_left", 90 * math.Pi / 180 * halfTrackMM}, {"turn_right", 90 * math.Pi / 180 * halfTrackMM}} {
		s := newSim(t)
		s.brk = 0.45
		s.run(c.name, false, 300, -1, -1)
		dl := float64(s.l) * mmPerTick
		dr := -float64(s.rr) * mmPerTick
		got := (dl + dr) / 2
		if c.name == "turn_left" || c.name == "turn_right" {
			got = math.Abs(dr-dl) / 2
		}
		if math.Abs(got) < math.Abs(c.wantMM)*0.9 || s.done != "ok" {
			t.Fatalf("%s: moved %.1f of %.1f, done %q notes %v", c.name, got, c.wantMM, s.done, s.r.notes)
		}
		if math.Abs(got) > math.Abs(c.wantMM)+25 {
			t.Fatalf("%s overshot: %.1f", c.name, got)
		}
	}
}

// Voice moves run at the configured speed (default 120 mm/s, was 40) and
// slow down when the ToF sees something ahead.
func TestDriveSpeedAndObstacleSlowdown(t *testing.T) {
	s := newSim(t)
	s.brk = 0.45
	s.run("forward_test", false, 250, -1, -1)
	if s.maxV < 70 || s.maxV > drivectl.Cfg.Drive*1.3 {
		t.Fatalf("open floor max %.0f mm/s (drive %.0f)", s.maxV, drivectl.Cfg.Drive)
	}
	near := newSim(t)
	near.brk = 0.45
	near.prox = obstacleMM + 20
	near.run("forward_test", false, 250, -1, -1)
	if near.maxV > 60 || near.maxV >= s.maxV {
		t.Fatalf("near obstacle %.0f mm/s (open %.0f)", near.maxV, s.maxV)
	}
	// backing up ignores the front ToF
	back := newSim(t)
	back.brk = 0.45
	back.prox = obstacleMM + 20
	back.run("backup", false, 250, -1, -1)
	if back.done != "ok" {
		t.Fatalf("backup %q", back.done)
	}
}
