"""Dance to the beat (the DDL/Anki 'DanceToTheBeat' behaviour, rebuilt on the hub).

Original (wire-os-victor engine): the anim process ran an aubio tempo tracker on
the mic stream; BeatDetectorComponent called a beat "detected" when, over the
last ~9-10 s, there were >= 6 beats, the latest with confidence >= 0.18 and the
tempo steady within 5 BPM. BehaviorDanceToTheBeat then queued anim_dancebeat_*
moves (headlift, headnod, eyebeat, headliftbody_left/right_small/med/large,
pivot, scoot), each started a little before the predicted next beat, and it
stopped when the beat was lost during its "listening" moves.

Here (numpy only, CPU, ~1 ms per 0.5 s of audio):
  BeatTracker  - spectral-flux onset envelope (100 Hz) + autocorrelation tempo
                 (70-180 BPM) + comb-filter phase -> bpm, confidence, next beat.
  MusicGate    - "music playing": the same steadiness rule as the original.
  dance_bar()  - one bar (4 beats) of DDL dancebeat keyframes as a plan text,
                 scaled to the beat period (head/lift deltas from the clips).
  hum_notes() / beeps() - "hum this": pitch track, quantise, robot beeps.
"""
from __future__ import annotations

import math
import random

import numpy as np

SR = 16000
HOP = 160  # 10 ms
FPS = SR // HOP
WIN = 512
MIN_BPM, MAX_BPM = 70.0, 180.0
# flux frames register an onset ~27 ms late (window + local-mean filter),
# measured on click tracks 90-150 BPM (std 5 ms)
ONSET_DELAY_S = 0.027


class BeatTracker:
    def __init__(self, secs: float = 10.0):
        self.keep = int(secs * FPS)
        self.env = np.zeros(0)
        self.rms = np.zeros(0)
        self.frames = 0  # total frames seen
        self._tail = np.zeros(0)
        self._prev = None
        self._win = np.hanning(WIN)

    def push(self, pcm: bytes | np.ndarray, noisy: bool = False) -> None:
        """noisy: the robot flagged its own motors in this chunk. Measured
        12 Oct: his head motors were 5-20x louder at the mic than the music and
        dropped the beat confidence from 0.6 to 0.25, so those frames are
        clamped to the recent median onset level instead of counted."""
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768 if isinstance(pcm, (bytes, bytearray)) else pcm
        x = np.concatenate([self._tail, x])
        n = (len(x) - WIN) // HOP + 1
        if n <= 0:
            self._tail = x
            return
        idx = np.arange(WIN)[None, :] + HOP * np.arange(n)[:, None]
        fr = x[idx]
        mag = np.log1p(100 * np.abs(np.fft.rfft(fr * self._win, axis=1)))
        prev = np.vstack([self._prev[None, :] if self._prev is not None else mag[:1], mag[:-1]])
        flux = np.maximum(0, mag - prev).sum(axis=1)
        self._prev = mag[-1]
        if noisy and len(self.env) > FPS:
            flux = np.minimum(flux, float(np.median(self.env[-2 * FPS:])))
        self.env = np.concatenate([self.env, flux])[-self.keep:]
        self.rms = np.concatenate([self.rms, np.sqrt((fr ** 2).mean(axis=1))])[-self.keep:]
        self.frames += n
        self._tail = x[n * HOP:]

    def tempo(self) -> tuple[float, float, float]:
        """-> (bpm, confidence 0..1, phase: frames since the last beat)."""
        e = self.env
        if len(e) < 4 * FPS:
            return 0.0, 0.0, 0.0
        e = e - np.convolve(e, np.ones(25) / 25, mode="same")  # local mean removed
        e = np.maximum(e, 0)
        if e.std() < 1e-6:
            return 0.0, 0.0, 0.0
        e = (e - e.mean()) / e.std()
        ac = np.correlate(e, e, mode="full")[len(e) - 1:] / len(e)
        lo, hi = int(FPS * 60 / MAX_BPM), int(FPS * 60 / MIN_BPM) + 1
        lags = np.arange(lo, min(hi, len(ac) // 2))
        if len(lags) == 0:
            return 0.0, 0.0, 0.0
        # score a lag with its multiples (beat periodicity), mild prior at 120 BPM
        sc = ac[lags] + 0.5 * ac[np.minimum(2 * lags, len(ac) - 1)] + 0.25 * ac[np.minimum(4 * lags, len(ac) - 1)]
        bpm = 60.0 * FPS / lags
        sc = sc * np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.9) ** 2)
        i = int(np.argmax(sc))
        lag = lags[i]
        # parabolic refinement
        if 0 < i < len(sc) - 1:
            a, b, c = sc[i - 1], sc[i], sc[i + 1]
            den = a - 2 * b + c
            lagf = lag + (0.5 * (a - c) / den if abs(den) > 1e-9 else 0.0)
        else:
            lagf = float(lag)
        conf = float(max(0.0, min(1.0, ac[lag] / max(ac[0], 1e-9))))
        # phase: comb over the last 4 s
        seg = self.env[-4 * FPS:]
        best, ph = -1.0, 0
        for off in range(lag):
            pos = np.arange(len(seg) - 1 - off, -1, -lagf).astype(int)
            s = seg[pos].sum()
            if s > best:
                best, ph = s, off
        return 60.0 * FPS / lagf, conf, float(ph)

    def next_beats(self, now_frame_offset: float = 0.0, n: int = 4) -> tuple[float, list[float]]:
        """Seconds from 'now' (end of the pushed audio) to the next n beats."""
        bpm, conf, ph = self.tempo()
        if bpm <= 0:
            return 0.0, []
        per = 60.0 / bpm
        first = (per - (ph / FPS) % per - ONSET_DELAY_S) % per
        return bpm, [first + k * per for k in range(n)]

    def on_beat_ratio(self, bpm: float, secs: float = 2.0) -> float:
        """Onset strength at the beat grid / average, over the last secs
        (DDL 'listening' check: is the beat still there?)."""
        if bpm <= 0 or len(self.env) < int(secs * FPS):
            return 0.0
        seg = self.env[-int(secs * FPS):]
        per = 60.0 * FPS / bpm
        best = 0.0
        for off in np.linspace(0, per, 12, endpoint=False):
            pos = np.arange(off, len(seg), per).astype(int)
            near = np.concatenate([seg[max(0, p - 2):p + 3] for p in pos])
            best = max(best, float(near.mean()))
        return best / max(float(seg.mean()), 1e-9)


class MusicGate:
    """Original rule: steady tempo (+-5 BPM, octave errors folded) over the
    last `need_s`, confidence >= thr every time, audible level."""

    def __init__(self, need_s: float = 6.0, thr: float = 0.18, min_rms: float = 0.0008, step_s: float = 0.5):
        self.need = max(1, int(need_s / step_s))
        self.thr, self.min_rms = thr, min_rms
        self.hist: list[tuple[float, float, float]] = []

    def update(self, bpm: float, conf: float, rms: float) -> bool:
        self.hist = (self.hist + [(bpm, conf, rms)])[-self.need:]
        return self.playing()

    def playing(self, need: int = 0) -> bool:
        """need: evaluations required (default the full window; a voice
        request uses a shorter one, like the original 'possible beat')."""
        need = need or self.need
        if len(self.hist) < need:
            return False
        hist = self.hist[-need:]
        bpms = [b for b, c, r in hist]
        if min(bpms) <= 0 or any(c < self.thr for _, c, _ in hist) or np.median([r for *_, r in hist]) < self.min_rms:
            return False
        ref = bpms[-1]
        def fold(b):
            while b > ref * 1.4:
                b /= 2
            while b < ref / 1.4:
                b *= 2
            return b
        return max(abs(fold(b) - ref) for b in bpms) <= 5.0

    def bpm(self) -> float:
        return self.hist[-1][0] if self.hist else 0.0


# ---- moves: anim_dancebeat_*_01 (vector-animations-build), one beat each.
# Keyframes (lift height mm / head angle deg, absolute) over ~330 ms:
#   headlift: lift 32->45->32, head 31->35->7->9      (head up a bit, dip, back)
#   headnod:  lift still,      head 31->35->7->9
#   headliftbody_left/right_small: + TURN_IN_PLACE ~6 deg
#   eyebeat: eyes only
# Here as deltas from where the dance started; times are fractions of a beat.
# Live 12 Oct (robot log): gesture steps end when the target is reached
# (12 deg dip in 176-199 ms), not at their nominal ms, so a multi-beat plan
# drifts within the bar; a +4 deg "up" step from rest never moved (5 of 33
# counts). So: one tiny plan PER BEAT, sent at its own time, each starting with
# the accent (dip / lift bump). Dancing pose: head up POSE_DEG first (the DDL
# clips hold the head ~30 deg up and dip to ~7), dips go from the pose.
ACCENT_S = 0.18  # measured time for the accent step to arrive
POSE_DEG = 16
DIP_DEG = POSE_DEG - 12
MOVES = {
    "headnod": [("headdeg", DIP_DEG, 0.30), ("headdeg", POSE_DEG, 0.40)],
    "headlift": [("headdeg", DIP_DEG, 0.30), ("liftmm", 13, 0.20), ("headdeg", POSE_DEG, 0.20), ("liftmm", 0.5, 0.20)],
    "liftbeat": [("liftmm", 13, 0.30), ("liftmm", 0.5, 0.40)],
    "body_left": [("headdeg", DIP_DEG + 4, 0.30), ("turn", 8, 0.35), ("headdeg", POSE_DEG, 0.20)],
    "body_right": [("headdeg", DIP_DEG + 4, 0.30), ("turn", -8, 0.35), ("headdeg", POSE_DEG, 0.20)],
    "eyebeat": [],
}
EYES = ["expr_happy", "expr_excited", "expr_love", "expr_proud"]


def beat_period(bpm: float) -> float:
    """Seconds between the beats he moves on (fast songs in half time)."""
    per = 60.0 / max(MIN_BPM, min(MAX_BPM, bpm))
    return per * 2 if per < 0.45 else per


def dance_beats(bpm: float, *, on_charger: bool | None, wheels: bool, lift: bool = True, head_only: bool = False,
                rng: random.Random | None = None, beats: int = 4, listen: bool = False) -> list[str]:
    """One bar = `beats` tiny plans, one per (half-time) beat, each sent so its
    first step lands on the beat. All use keepbase (deltas from the dance's
    rest base). on_charger (or unknown) -> no lift, no wheels."""
    rng = rng or random.Random()
    on = on_charger is not False
    pool = ["headnod"] if head_only else ["headnod", "headnod", "headlift", "liftbeat", "eyebeat"]
    if on or not lift:
        pool = [m for m in pool if m not in ("headlift", "liftbeat")] or ["headnod"]
    use_wheels = wheels and not on and not head_only
    per = beat_period(bpm)
    face = rng.choice(EYES)
    out = []
    for b in range(beats):
        m = "eyebeat" if listen else rng.choice(pool)
        if use_wheels and not listen and b == beats - 1:
            m = rng.choice(["body_left", "body_right"])
        steps = ["keepbase"]
        if b == 0 and not head_only:
            steps.append(f"face {'expr_curious' if listen else face}")
        for kind, v, frac in MOVES[m]:
            ms = max(80, int(frac * per * 1000))
            steps.append(f"turn {v}" if kind == "turn" else f"{kind} {v} {ms}")
        if len(steps) == 1 or steps[-1].startswith("face"):
            steps.append(f"headdeg {POSE_DEG} 150")  # hold the pose (keeps the base window alive)
        out.append("plan:" + ";".join(steps))
    return out


def dance_bar(bpm: float, *, on_charger: bool | None, wheels: bool, lift: bool = True, head_only: bool = False,
              rng: random.Random | None = None, beats: int = 4) -> tuple[str, float]:
    """The bar's beats joined (tests / inspection) and the bar length in s."""
    per = beat_period(bpm)
    if per > 60.0 / max(MIN_BPM, min(MAX_BPM, bpm)) + 1e-9:
        beats = max(1, beats // 2)
    pl = dance_beats(bpm, on_charger=on_charger, wheels=wheels, lift=lift, head_only=head_only, rng=rng, beats=beats)
    steps = [st for p in pl for st in p[5:].split(";") if st != "keepbase"]
    return "plan:" + ";".join(steps), per * beats


# ---- hum along: pitch track a clip and play it back as beeps.
def hum_notes(pcm: bytes, sr: int = SR, fmin: float = 80, fmax: float = 1000) -> list[tuple[float, float]]:
    """-> [(hz, seconds)], quantised to semitones, short blips merged."""
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768
    fl, hop = int(0.04 * sr), int(0.02 * sr)
    if len(x) < fl:
        return []
    floor = max(0.01, 0.15 * np.percentile(np.abs(x), 95))
    notes: list[list[float]] = []
    for i in range(0, len(x) - fl, hop):
        f = x[i:i + fl] - x[i:i + fl].mean()
        hz = 0.0
        if np.sqrt((f ** 2).mean()) > floor * 0.5:
            ac = np.correlate(f, f, mode="full")[fl - 1:]
            lo, hi = int(sr / fmax), min(int(sr / fmin), fl - 1)
            j = lo + int(np.argmax(ac[lo:hi]))
            if ac[0] > 0 and ac[j] / ac[0] > 0.5:
                hz = sr / j
        if hz > 0:
            hz = 440.0 * 2 ** (round(12 * math.log2(hz / 440.0)) / 12)
        if notes and abs(notes[-1][0] - hz) < 1e-6:
            notes[-1][1] += hop / sr
        else:
            notes.append([hz, hop / sr])
    # merge blips < 60 ms into the previous note
    out: list[list[float]] = []
    for hz, d in notes:
        if out and d < 0.06:
            out[-1][1] += d
        else:
            out.append([hz, d])
    while out and out[0][0] == 0:
        out.pop(0)
    while out and out[-1][0] == 0:
        out.pop()
    return [(h, d) for h, d in out][:40]


def beeps(notes: list[tuple[float, float]], sr: int = SR) -> np.ndarray:
    """Vector-style tones: square+sine, moved into 500-2000 Hz, soft edges."""
    out = []
    for hz, d in notes:
        n = int(d * sr)
        if hz <= 0:
            out.append(np.zeros(n))
            continue
        while hz < 500:
            hz *= 2
        while hz > 2000:
            hz /= 2
        t = np.arange(n) / sr
        y = 0.6 * np.sin(2 * np.pi * hz * t) + 0.25 * np.sign(np.sin(2 * np.pi * hz * t))
        env = np.minimum(1, np.minimum(np.arange(n) / (0.01 * sr), (n - np.arange(n)) / (0.02 * sr)))
        out.append(y * np.clip(env, 0, 1))
    return np.concatenate(out) if out else np.zeros(0)
