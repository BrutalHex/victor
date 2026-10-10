package action

import (
	"math"
	"strings"
	"testing"
	"time"
)

func kinds(p Plan) []stepKind {
	var k []stepKind
	for _, s := range p.Steps {
		k = append(k, s.kind)
	}
	return k
}

func TestParsePlanClamps(t *testing.T) {
	p, notes := ParsePlan("plan:drive 9000;turn -5000;wait 99999;head up;lift down;face expr_happy")
	if len(notes) != 0 {
		t.Fatalf("notes %v", notes)
	}
	if p.Steps[0].mm != MaxDriveMM || p.Steps[1].deg != -MaxTurnDeg || p.Steps[2].dur != MaxWait {
		t.Fatalf("not clamped: %+v", p.Steps[:3])
	}
	if p.Timeout != MaxPlanTime {
		t.Fatalf("timeout %v", p.Timeout)
	}
	if p.Steps[5].clip != "expr_happy" || p.Steps[5].kind != stWait || p.Steps[5].dur != 0 {
		t.Fatalf("face step %+v", p.Steps[5])
	}
}

func TestParsePlanDropsBadSteps(t *testing.T) {
	ValidClip = func(c string) bool { return c == "expr_sad" }
	defer func() { ValidClip = func(string) bool { return true } }()
	p, notes := ParsePlan("plan:drive x;drive nan;turn inf;fly 3;head sideways;lift 2;wait -1;face nope;face expr_sad;trick leave_charger;trick forward_test;trick stop;rm -rf")
	if len(p.Steps) != 1 || p.Steps[0].clip != "expr_sad" {
		t.Fatalf("steps %+v", p.Steps)
	}
	if len(notes) < 10 {
		t.Fatalf("notes %v", notes)
	}
}

func TestParsePlanStepCap(t *testing.T) {
	p, notes := ParsePlan("plan:" + strings.Repeat("head up;", 20))
	if len(p.Steps) != MaxSteps {
		t.Fatalf("%d steps", len(p.Steps))
	}
	if len(notes) != 1 || !strings.Contains(notes[0], "12 steps") {
		t.Fatalf("notes %v", notes)
	}
}

func TestParsePlanTravelCap(t *testing.T) {
	p, notes := ParsePlan("plan:drive 500;drive -500;drive 500;drive 500;drive 500;turn 90")
	mm := 0.0
	for _, s := range p.Steps {
		mm += math.Abs(s.mm)
	}
	if mm > MaxTravelMM || len(p.Steps) != 4 {
		t.Fatalf("travel %.0f steps %d", mm, len(p.Steps))
	}
	if len(notes) == 0 || !strings.Contains(notes[0], "travel") {
		t.Fatalf("notes %v", notes)
	}
}

func TestParsePlanTimeCap(t *testing.T) {
	p, notes := ParsePlan("plan:wait 5000;wait 5000;wait 5000;wait 5000;wait 5000;wait 5000;wait 5000")
	if len(p.Steps) != 6 || len(notes) != 1 || !strings.Contains(notes[0], "longer") {
		t.Fatalf("steps %d notes %v", len(p.Steps), notes)
	}
}

func TestSquareFitsCaps(t *testing.T) {
	p, notes := ParsePlan("plan:drive 300;turn 90;drive 300;turn 90;drive 300;turn 90;drive 300;turn 90")
	if len(notes) != 0 || len(p.Steps) != 8 {
		t.Fatalf("square cut: %v", notes)
	}
	spin, notes := ParsePlan("plan:turn 720")
	if len(notes) != 0 || spin.Steps[0].deg != 720 {
		t.Fatalf("spin %v", notes)
	}
}

func TestTrickExpandsNamedPlan(t *testing.T) {
	p, _ := ParsePlan("plan:trick dance;head nod")
	if len(p.Steps) != len(Plans["dance"].Steps)+3 {
		t.Fatalf("%v", kinds(p))
	}
}

func TestPlanRunsSquareOffCharger(t *testing.T) {
	s := newSim(t)
	s.run("plan:drive 100;turn 90;drive 100;turn 90", false, 1500, -1, -1)
	if !strings.HasPrefix(s.done, "ok") || s.r.Active() {
		t.Fatalf("done %q", s.done)
	}
	if s.maxW == 0 {
		t.Fatal("never drove")
	}
}

func TestPlanHeldOnCharger(t *testing.T) {
	s := newSim(t)
	s.run("plan:drive 300;turn 180;head up;lift up", true, 800, -1, -1)
	if s.maxW != 0 {
		t.Fatalf("drove on charger %d", s.maxW)
	}
	if !strings.Contains(s.done, "wheels held") || !strings.Contains(s.done, "lift skipped") {
		t.Fatalf("done %q", s.done)
	}
}

func TestPlanVetoAborts(t *testing.T) {
	s := newSim(t)
	s.run("plan:drive 400;turn 90", false, 600, 30, -1)
	if !strings.HasPrefix(s.done, "aborted") {
		t.Fatalf("done %q", s.done)
	}
	if last := s.outs[len(s.outs)-1]; last.PWM != [4]int16{} {
		t.Fatalf("motors on %v", last.PWM)
	}
}

func TestPlanCancelStopsAtOnce(t *testing.T) {
	s := newSim(t)
	s.r.StartText("plan:drive 400;turn 720", s.now)
	for i := 0; i < 20; i++ {
		s.r.Tick(In{Now: s.now.Add(time.Duration(i) * 20 * time.Millisecond), EncL: int32(i * 5), EncR: int32(-i * 5)})
	}
	if !s.r.Cancel("voice stop") || s.r.Active() {
		t.Fatal("not cancelled")
	}
	if o := s.r.Tick(In{Now: s.now.Add(time.Second)}); o.PWM != [4]int16{} || o.Active {
		t.Fatalf("still driving %+v", o)
	}
	if s.r.Cancel("again") {
		t.Fatal("double cancel")
	}
}

func TestPlanTimeout30s(t *testing.T) {
	var r Runner
	t0 := time.Unix(0, 0)
	r.StartText("plan:drive 500", t0) // wheels never move (stalled sim)
	o := r.Tick(In{Now: t0.Add(31 * time.Second)})
	if !strings.Contains(o.Done, "timeout") {
		t.Fatalf("%+v", o)
	}
}

func TestEmptyPlanRefused(t *testing.T) {
	var r Runner
	if r.StartText("plan:", time.Now()) || r.StartText("plan:fly away", time.Now()) || r.Active() {
		t.Fatal("empty plan started")
	}
}

func TestFaceStepsDoNotCount(t *testing.T) {
	p, notes := ParsePlan("plan:face expr_happy;" + strings.Repeat("drive 100;turn 60;", 6) + "face expr_proud")
	if len(notes) != 0 || len(p.Steps) != 14 {
		t.Fatalf("steps %d notes %v", len(p.Steps), notes)
	}
}
