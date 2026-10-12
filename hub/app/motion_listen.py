"""Listen while moving: pause the wheels when someone is probably talking.

Motor noise drowns speech on Vector's body mics: live 10 Oct 15:36, every
utterance during explore came back from STT as "". So while the wheels run
(the agent flags those audio packets FlagWheels) a spectral detector looks
for a likely speech onset against a motor-noise profile it learns while
driving, not for plain loudness:

  * per band (20 log bands, 250-4000 Hz) the log power is compared with the
    learned motor profile; a speech frame needs >= MIN_BANDS voice-band bands
    (300-3400 Hz) BAND_DB over it,
  * and harmonicity: autocorrelation (80-400 Hz pitch) of the frame after
    spectral subtraction of the motor profile, so a steady gear whine does
    not count as a voice but a vowel on top of it does,
  * an onset is ONSET_FRAMES speech frames within the last 8 (~100-160 ms).

On an onset the hub sends "hold:<ms>" (the agent soft-brakes in 150 ms and
freezes the plan / explore), the VAD turn opens with a pre-roll so the first
word is kept, and the turn runs as usual. Afterwards "release": a paused plan
continues; a stop or a new command already replaced it on the robot.
Backup: a short listen window (wheels quiet LISTEN_MS) after EVERY_S of
continuous wheel noise. Frames during our own speech, within the agent's
ramp flag (200 ms after a start/stop/flip/head move) and while held are
ignored. Settings: HUB_MOTION_* (see .env.example).
"""
from __future__ import annotations

import glob
import os
import random
import threading
import time

import numpy as np

RATE = 16000
FRAME = 320  # 20 ms
NFFT = 512


def _f(k: str, d: str) -> float:
    try:
        return float(os.environ.get(k, d))
    except ValueError:
        return float(d)


def _on(k: str, d: str) -> bool:
    return os.environ.get(k, d).strip().lower() not in ("0", "false", "no", "off", "")


ENABLED = _on("HUB_MOTION_LISTEN", "1")
BAND_DB = _f("HUB_MOTION_BAND_DB", "7")
MIN_BANDS = int(_f("HUB_MOTION_MIN_BANDS", "4"))
HARM = _f("HUB_MOTION_HARM", "0.45")
ONSET_FRAMES = int(_f("HUB_MOTION_ONSET_FRAMES", "4"))
PREROLL_MS = int(_f("HUB_MOTION_PREROLL_MS", "600"))
HOLD_MS = int(_f("HUB_MOTION_HOLD_MS", "3000"))  # one hold; refreshed while the turn runs
RESUME_S = _f("HUB_MOTION_RESUME_S", "1.0")  # nothing intelligible: drive on after this
WAKE_HOLD_S = _f("HUB_MOTION_WAKE_HOLD_S", "6")  # after "Hey Vector" while moving: wait for the command
EVERY_S = _f("HUB_MOTION_LISTEN_EVERY_S", "7")  # 0 = no periodic listen windows
LISTEN_MS = int(_f("HUB_MOTION_LISTEN_MS", "700"))
REFRACTORY_S = _f("HUB_MOTION_REFRACTORY_S", "1.5")
REC_ON = _on("HUB_MOTION_REC", "1")  # keep a few motor-noise clips for offline tuning
REC_DIR = os.environ.get("HUB_MOTION_REC_DIR", "/app/data/rec")
REC_SECS, REC_KEEP = 30, 6

_HZ = np.fft.rfftfreq(NFFT, 1 / RATE)
_EDGES = np.geomspace(250, 4000, 21)
_BAND_BINS = [np.where((_HZ >= lo) & (_HZ < hi))[0] for lo, hi in zip(_EDGES[:-1], _EDGES[1:])]
_CENTRES = np.sqrt(_EDGES[:-1] * _EDGES[1:])
_VOICE = np.where((_CENTRES >= 300) & (_CENTRES <= 3400))[0]
_WIN = np.hanning(FRAME)
_SUB = (_HZ >= 80) & (_HZ <= 4000)


class Detector:
    """Per-frame speech-onset detector against a learned motor-noise profile."""

    def __init__(self, band_db: float = BAND_DB, min_bands: int = MIN_BANDS, harm: float = HARM,
                 onset_frames: int = ONSET_FRAMES):
        self.band_db, self.min_bands, self.harm, self.onset_frames = band_db, min_bands, harm, onset_frames
        self.prof_db: np.ndarray | None = None  # per band, dB
        self.prof_bin: np.ndarray | None = None  # per bin, linear power
        self.learned = 0
        self.recent: list[int] = []  # 1 = speech frame, last 8
        self.last = {"over": 0, "harm": 0.0, "speech": False}
        self.frames = self.onsets = 0

    def reset_run(self) -> None:
        self.recent = []

    def armed(self) -> bool:
        return self.learned >= 10

    def feed(self, pcm: bytes, ramp: bool = False) -> bool:
        """One 20 ms motor-noise frame. True = likely speech onset now."""
        x = np.frombuffer(pcm[: FRAME * 2], dtype="<i2").astype(np.float64)
        if len(x) < FRAME:
            return False
        self.frames += 1
        spec = np.abs(np.fft.rfft((x - x.mean()) * _WIN, NFFT)) ** 2 + 1e-3
        bands = np.array([spec[b].sum() for b in _BAND_BINS])
        db = 10 * np.log10(bands + 1e-3)
        if self.prof_db is None:
            self.prof_db, self.prof_bin = db.copy(), spec.copy()
            self.learned = 1
            return False
        ex = db - self.prof_db
        over = int((ex[_VOICE] > self.band_db).sum())
        h = 0.0
        if over >= self.min_bands:
            # harmonicity of what is left after removing the motor profile
            s = np.where(_SUB, np.maximum(spec - 2.0 * self.prof_bin, 0.0), 0.0)
            r = np.fft.irfft(s, NFFT)
            if r[0] > 0:
                h = float(r[40:201].max() / r[0])
        speech = over >= self.min_bands and h >= self.harm and not ramp
        self.last = {"over": over, "harm": round(h, 2), "speech": speech}
        if ramp:
            self.recent = []
            return False
        # learn the motor profile on frames that are not speech candidates;
        # quieter-than-profile frames pull it down faster (min tracking)
        if over < max(2, self.min_bands // 2):
            a = 0.3 if self.learned < 10 else 0.05
            down = db < self.prof_db
            self.prof_db = np.where(down, self.prof_db + 2 * a * (db - self.prof_db), self.prof_db + a * (db - self.prof_db))
            self.prof_bin = self.prof_bin + a * (spec - self.prof_bin)
            self.learned += 1
        if not self.armed():
            return False
        self.recent.append(1 if speech else 0)
        if len(self.recent) > 8:
            self.recent.pop(0)
        if speech and sum(self.recent) >= self.onset_frames:
            self.onsets += 1
            self.recent = []
            return True
        return False


class Listener:
    """Hold / release state for the hub. send(name) queues an agent action."""

    def __init__(self, send, enabled: bool = ENABLED, clock=time.time):
        self.send, self.enabled, self.clock = send, enabled, clock
        self.det = Detector()
        self.lock = threading.RLock()
        self.pre: list[bytes] = []
        self.held = False
        self.kind = ""  # onset | window | wake
        self.capture = False  # VAD turn opened by (or during) a hold
        self.turn = False  # an utterance from a hold is being handled
        self.refresh_at = self.release_at = 0.0
        self.moving_since = self.last_wheels = 0.0
        self.quiet_until = 0.0
        self.stats = {"onsets": 0, "windows": 0, "holds": 0, "resumed": 0, "kept": 0, "last": ""}
        self.rec: list[bytes] = []
        self.rec_flags: list[str] = []
        self.next_window = 0.0

    # -- audio --------------------------------------------------------------
    def preroll(self) -> bytes:
        return b"".join(self.pre)

    def on_frame(self, pcm: bytes, wheels: bool, ramp: bool, own_sound: bool, vad_active: bool) -> bool:
        """Every audio frame. True: open the VAD turn now (with preroll())."""
        if not self.enabled:
            return False
        now = self.clock()
        with self.lock:
            self.pre.append(pcm)
            if len(self.pre) > max(1, PREROLL_MS // 20):
                self.pre.pop(0)
            if wheels and not self.held:
                self._record(pcm, ramp)
                if now - self.last_wheels > 0.3:
                    self.moving_since = now
                    self.next_window = now + EVERY_S + random.uniform(-1.0, 1.0)
                self.last_wheels = now
            if self.held:
                if vad_active and self.kind == "window":
                    self.kind, self.capture = "onset", True  # someone talks in the window: keep still
                    self.release_at = 0.0
                    self._refresh(now, force=True)
                return False
            if not wheels or own_sound or now < self.quiet_until:
                self.det.reset_run()
                return False
            if self.det.feed(pcm, ramp):
                self.stats["onsets"] += 1
                self._hold(now, "onset")
                self.capture = True
                return not vad_active
            if EVERY_S > 0 and now >= self.next_window and not vad_active:
                self.stats["windows"] += 1
                self._hold(now, "window", LISTEN_MS)
                self.release_at = now + LISTEN_MS / 1000.0
            return False

    # -- turn ---------------------------------------------------------------
    def utterance(self, passed: bool) -> None:
        """The VAD closed a turn that a hold opened (or that ran during one)."""
        with self.lock:
            if not self.held:
                return
            if passed:
                self.turn, self.capture = True, False
                self._refresh(self.clock(), force=True)
            else:
                self.capture = False
                self._release_in(RESUME_S, "noise")

    def turn_done(self, spoke_s: float = 0.0, woke: bool = False) -> None:
        with self.lock:
            if not self.held:
                self.turn = False
                return
            self.turn = False
            if woke:
                self.kind = "wake"
                self.release_at = self.clock() + WAKE_HOLD_S
                self._refresh(self.clock(), force=True)
                return
            self._release_in(spoke_s + 0.3 if spoke_s > 0 else RESUME_S, "turn")

    def in_turn(self) -> bool:
        return self.held and (self.turn or self.capture)

    def tick(self) -> None:
        """~10 Hz: keep the hold alive while a turn needs it; release on time."""
        now = self.clock()
        with self.lock:
            if not self.held:
                return
            if self.release_at and now >= self.release_at:
                self.held, self.turn, self.capture, self.release_at = False, False, False, 0.0
                self.quiet_until = now + 0.3  # restart clunk
                self.det.reset_run()
                self.stats["resumed"] += 1
                self.send("release")
                print(f"motion release ({self.stats['last']})", flush=True)
                return
            self._refresh(now)

    def status(self) -> dict:
        with self.lock:
            return dict(self.stats, held=self.held, kind=self.kind, turn=self.turn, capture=self.capture,
                        armed=self.det.armed(), last_frame=self.det.last, enabled=self.enabled)

    # -- internals ----------------------------------------------------------
    def _hold(self, now: float, kind: str, ms: int = HOLD_MS) -> None:
        self.held, self.kind, self.release_at = True, kind, 0.0
        self.stats["holds"] += 1
        self.stats["last"] = kind
        self.refresh_at = now + ms / 1000.0 * 0.5
        self.send(f"hold:{ms}")
        print(f"motion hold {kind} {ms}ms frame={self.det.last}", flush=True)

    def _refresh(self, now: float, force: bool = False) -> None:
        if self.kind == "window" and not force:
            return  # a listen window just expires (release_at)
        if force or now >= self.refresh_at:
            self.refresh_at = now + HOLD_MS / 1000.0 * 0.5
            self.send(f"hold:{HOLD_MS}")

    def _release_in(self, s: float, why: str) -> None:
        self.release_at = self.clock() + max(0.0, s)
        self.stats["last"] = why
        self._refresh(self.clock(), force=True)  # cover the wait

    def _record(self, pcm: bytes, ramp: bool) -> None:
        if not REC_ON:
            return
        self.rec.append(pcm)
        self.rec_flags.append("r" if ramp else "w")
        if len(self.rec) * FRAME >= REC_SECS * RATE:
            pcm_all, flags = b"".join(self.rec), "".join(self.rec_flags)
            self.rec, self.rec_flags = [], []
            threading.Thread(target=_save_motor, args=(pcm_all, flags), daemon=True).start()


def _save_motor(pcm: bytes, flags: str) -> None:
    try:
        import wave
        os.makedirs(REC_DIR, exist_ok=True)
        old = sorted(glob.glob(os.path.join(REC_DIR, "motor-*.wav")))
        for p in old[: max(0, len(old) - REC_KEEP + 1)]:
            os.remove(p)
            if os.path.exists(p[:-4] + ".flags"):
                os.remove(p[:-4] + ".flags")
        path = os.path.join(REC_DIR, time.strftime("motor-%Y%m%d-%H%M%S.wav"))
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        with open(path[:-4] + ".flags", "w") as f:
            f.write(flags)
        print(f"motion rec saved {path} s={len(pcm) / 2 / RATE:.0f}", flush=True)
    except OSError as exc:
        print(f"motion rec failed {exc!r}", flush=True)
