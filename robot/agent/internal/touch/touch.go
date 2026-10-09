// Package touch turns the backpack capacitive level into touch / petting
// events, modelled on the stock engine's TouchSensorComponent
// (vector engine/components/sensors/touchSensorComponent.cpp): 5-sample box
// filter, slow baseline that only follows while untouched, detect at
// baseline+65 and release below baseline+45 (hysteresis), a press confirmed
// after 4 consecutive samples, and a re-baseline whenever the charger state
// changes (the level shifts on the contacts). It never looks at the button.
package touch

import "time"

const (
	boxN           = 5
	detectOff      = 65 // stock: minUndetect 45 + gap 20
	undetectOff    = 45
	confirmN       = 4
	calibN         = 60 // samples to settle the baseline (~1.2 s at 50 Hz)
	maxBelow       = 25 // stock kAllowedDifferenceFromBaseline: a lower floor re-baselines
	fastCoeff      = 0.05
	slowCoeff      = 0.002
	PetAfter       = 400 * time.Millisecond // contact this long = petting, not a bump
	LevelEvery     = 1500 * time.Millisecond
	MaxLevel       = 4  // 1-4 like anim_petting_lvl1..4, then bliss
	StrokeDelta    = 18 // filtered swing that counts as a stroke while touched
	ReleaseTimeout = 30 * time.Second
)

type Event int

const (
	None     Event = iota
	Touched        // confirmed contact
	PetStart       // contact held PetAfter
	PetLevel       // level went up (Level())
	Released       // contact ended
)

func (e Event) String() string {
	return [...]string{"none", "touched", "pet_start", "pet_level", "released"}[e]
}

type Detector struct {
	box       [boxN]float64
	n         int
	baseline  float64
	calib     int
	pressed   bool
	confirmed bool
	streak    int
	charger   bool
	haveChg   bool
	since     time.Time
	petting   bool
	level     int
	levelAt   time.Time
	strokes   int
	lastV     float64
	extreme   float64
	rising    bool
	Filtered  float64
}

// Feed takes one raw sample (Frame.Touch) with the charger state.
func (d *Detector) Feed(now time.Time, raw uint16, onCharger bool) Event {
	if raw == 0 || raw == 0xFFFF {
		return None
	}
	if !d.haveChg || onCharger != d.charger {
		d.haveChg, d.charger = true, onCharger
		d.recalibrate()
	}
	d.box[d.n%boxN] = float64(raw)
	d.n++
	cnt := d.n
	if cnt > boxN {
		cnt = boxN
	}
	sum := 0.0
	for i := 0; i < cnt; i++ {
		sum += d.box[i]
	}
	v := sum / float64(cnt)
	d.Filtered = v
	if d.calib < calibN {
		if d.calib == 0 {
			d.baseline = v
		} else {
			d.baseline += fastCoeff * (v - d.baseline)
		}
		d.calib++
		return d.release(now)
	}
	// hysteresis
	switch {
	case v > d.baseline+detectOff:
		d.pressed = true
	case v < d.baseline+undetectOff:
		d.pressed = false
	}
	if !d.pressed {
		// follow slowly while untouched; drop fast if the floor fell
		if d.baseline-v > maxBelow {
			d.baseline += fastCoeff * (v - d.baseline)
		} else {
			d.baseline += slowCoeff * (v - d.baseline)
		}
	}
	if d.pressed != d.confirmed {
		d.streak++
		if d.streak >= confirmN {
			d.confirmed = d.pressed
			d.streak = 0
			if d.confirmed {
				d.since, d.levelAt, d.level, d.petting, d.strokes = now, now, 0, false, 0
				d.lastV, d.extreme, d.rising = v, v, true
				return Touched
			}
			d.petting = false
			return Released
		}
	} else {
		d.streak = 0
	}
	if !d.confirmed {
		return None
	}
	d.trackStroke(v)
	held := now.Sub(d.since)
	if held > ReleaseTimeout {
		// stuck high (something resting on the back): re-learn the floor
		d.recalibrate()
		return d.release(now)
	}
	if !d.petting && held >= PetAfter {
		d.petting, d.level, d.levelAt = true, 1, now
		return PetStart
	}
	if d.petting && d.level < MaxLevel+1 && now.Sub(d.levelAt) >= LevelEvery {
		d.level++
		d.levelAt = now
		return PetLevel
	}
	return None
}

func (d *Detector) trackStroke(v float64) {
	if d.rising {
		if v > d.extreme {
			d.extreme = v
		} else if d.extreme-v > StrokeDelta {
			d.rising, d.extreme = false, v
			d.strokes++
		}
	} else {
		if v < d.extreme {
			d.extreme = v
		} else if v-d.extreme > StrokeDelta {
			d.rising, d.extreme = true, v
		}
	}
}

func (d *Detector) release(now time.Time) Event {
	if d.confirmed || d.petting {
		d.confirmed, d.petting, d.pressed = false, false, false
		return Released
	}
	return None
}

func (d *Detector) recalibrate() {
	d.calib, d.n = 0, 0
	d.pressed, d.streak = false, 0
}

func (d *Detector) Touching() bool    { return d.confirmed }
func (d *Detector) Petting() bool     { return d.petting }
func (d *Detector) Level() int        { return d.level }
func (d *Detector) Strokes() int      { return d.strokes }
func (d *Detector) Baseline() float64 { return d.baseline }
func (d *Detector) Calibrated() bool  { return d.calib >= calibN }
