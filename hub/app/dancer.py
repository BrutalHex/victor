"""Hub side of 'dance to the beat' and 'hum along' (see music.py).

Audio: every 16 kHz mic chunk the hub receives is fed here (cheap: append to a
queue). A worker thread runs the beat tracker every 0.5 s and drives:
  - voice "dance to the music": listen (up to HUB_DANCE_LISTEN_S) for a steady
    beat, then dance bar by bar (DDL dancebeat moves, timed to the predicted
    beats, HUB_DANCE_LEAD_MS early for the robot's latency), every 4th bar a
    still "listening" bar (eyes only) that re-checks the beat on clean audio
    (his motors are quiet) - like BehaviorDanceToTheBeat's listening anims;
    the beat gone -> stop.
  - automatic: awake + HUB_MUSIC_AUTO + music steady for ~6 s + cooldown ->
    the same dance without wheels; asleep -> head nods only, no face, no sound.
  - "hum this": capture HUB_HUM_S of audio after his prompt, pitch track,
    play the tune back as beeps.
Never during his own playback (speaking() is True): those chunks are dropped.
"""
from __future__ import annotations

import os
import queue
import random
import threading
import time

import numpy as np

import music


def _envf(k: str, d: float) -> float:
    try:
        return float(os.environ.get(k, d))
    except ValueError:
        return d


def _on(k: str, d: str = "1") -> bool:
    return os.environ.get(k, d).strip().lower() not in ("0", "false", "no", "off")


class Dancer:
    def __init__(self, send_plan, on_charger, awake, speaking, say, play, log=print, clock=time.time, schedule=None):
        self.send_plan, self.on_charger, self.awake, self.speaking = send_plan, on_charger, awake, speaking
        self.say, self.play, self.log, self.clock = say, play, log, clock
        self.q: queue.Queue = queue.Queue(maxsize=400)
        self.bt = music.BeatTracker()
        self.gate = music.MusicGate(need_s=_envf("HUB_MUSIC_STEADY_S", 6.0), thr=_envf("HUB_MUSIC_CONF", 0.18))
        self.rng = random.Random()
        self.lock = threading.Lock()
        self.mode = ""  # "" | listen | dance | auto | asleep | hum
        self.lang = "en"
        self.wheels = False
        self.until = 0.0  # listen deadline / dance end
        self.next_bar = 0.0
        self.bar_n = 0
        self.cool_until = 0.0
        self.hum_buf = bytearray()
        self.hum_from = 0.0
        self.since_eval = 0
        self.last_eval = {}
        self._stop = False
        self.schedule = schedule or (lambda d, fn: threading.Timer(d, fn).start())
        self.listen_check_at = 0.0
        self._beat_s = 0.0
        self.audio_end = 0.0  # hub time the newest tracked audio arrived

    # -- config (read live so .env edits + restart apply)
    @property
    def auto(self) -> bool:
        return _on("HUB_MUSIC_AUTO", "1")

    # -- inputs
    def capturing(self) -> bool:
        return self.mode == "hum" and self.clock() >= self.hum_from

    def feed(self, pcm: bytes, noisy: bool = False) -> None:
        if self.speaking():
            return
        try:
            self.q.put_nowait((pcm, noisy, self.clock()))
        except queue.Full:
            pass

    def status(self) -> dict:
        return {"mode": self.mode, "bpm": round(self.gate.bpm(), 1), "playing": self.gate.playing(),
                "auto": self.auto, "cooldown_s": max(0, round(self.cool_until - self.clock())), **self.last_eval}

    # -- commands
    def start_dance(self, lang: str, wheels: bool) -> str:
        """Voice request. Returns the reply key."""
        with self.lock:
            self.lang, self.wheels = lang, wheels
            if self.gate.playing(6):
                self._begin("dance")
                return ""
            self.mode, self.until = "listen", self.clock() + _envf("HUB_DANCE_LISTEN_S", 12.0)
        self.send_plan("plan:face expr_curious;headdeg 6 300")
        return "dance_listen"

    def start_hum(self, lang: str, speech_s: float) -> None:
        with self.lock:
            self.lang = lang
            self.mode, self.hum_buf = "hum", bytearray()
            self.hum_from = self.clock() + speech_s
            self.until = self.hum_from + 20.0  # set to first audio + HUB_HUM_S below

    def stop(self, why: str = "stop") -> bool:
        with self.lock:
            was = self.mode
            if was in ("dance", "auto", "asleep"):
                self.cool_until = self.clock() + _envf("HUB_MUSIC_COOLDOWN_S", 300)
            self.mode = ""
        if was:
            self.log(f"music stop {was}: {why}")
        return bool(was)

    # -- internals
    def _begin(self, mode: str) -> None:
        self.mode, self.bar_n = mode, 0
        self.until = self.clock() + _envf("HUB_DANCE_MAX_S", 90.0 if mode == "dance" else 45.0)
        self.next_bar = 0.0
        self.log(f"music {mode} start bpm={self.gate.bpm():.1f}")

    def _bar(self, now: float) -> None:
        bpm = self.gate.bpm() or self.bt.tempo()[0]
        _, beats = self.bt.next_beats()
        if not beats:
            return
        # beats are relative to the end of the tracked audio (its arrival time,
        # not this tick). The accent (first step) must END on the beat: send
        # LEAD (audio + command latency, measured) + the accent time early.
        per = 60.0 / bpm
        if per < 0.45:
            per *= 2  # dance_bar dances fast songs in half time
        lead = _envf("HUB_DANCE_LEAD_MS", 60) / 1000 + music.ACCENT_FRAC * per
        ref = self.audio_end or now
        start = ref + beats[0] - lead
        while start < now + 0.02:
            start += per
        listen = self.bar_n % 4 == 3
        self._beat_s = 60.0 / bpm
        if listen:
            plan, dur = "plan:face expr_curious;wait %d" % int(4 * 60000 / bpm), 4 * 60.0 / bpm
        else:
            on = self.on_charger()
            plan, dur = music.dance_bar(bpm, on_charger=on, wheels=self.wheels and self.mode == "dance",
                                        head_only=self.mode == "asleep", rng=self.rng)
            if self.mode == "asleep":
                plan = "plan:" + ";".join(s for s in plan[5:].split(";") if not s.startswith("face"))
        if self.bar_n > 0:
            plan = plan.replace("plan:", "plan:keepbase;", 1)
        delay = max(0.0, start - now)
        mode = self.mode
        self.schedule(delay, lambda: self._send_if(mode, plan))
        self.next_bar = start + dur - 0.15  # plan the next bar before its send time
        self.bar_n += 1
        self.listen_check_at = start + dur - 0.1 if listen else 0.0

    def _send_if(self, mode: str, plan: str) -> None:
        if self.mode == mode and mode:
            self.log(f"music bar sent t={time.time():.3f} beat_s={self._beat_s:.3f}")
            self.send_plan(plan)

    def tick(self) -> None:
        """Process queued audio and act; called by the worker (and tests)."""
        n = 0
        while True:
            try:
                pcm, noisy, t_in = self.q.get_nowait()
            except queue.Empty:
                break
            self.bt.push(pcm, noisy)
            self.audio_end = t_in
            n += len(pcm) // 2
            if self.capturing():
                if not self.hum_buf:
                    self.until = min(self.until, self.clock() + _envf("HUB_HUM_S", 5.0))
                self.hum_buf += pcm
        self.since_eval += n
        now = self.clock()
        if self.since_eval >= music.SR // 2:
            self.since_eval = 0
            bpm, conf, _ = self.bt.tempo()
            rms = float(np.median(self.bt.rms[-200:])) if len(self.bt.rms) else 0.0
            playing = self.gate.update(bpm, conf, rms)
            self.last_eval = {"bpm_now": round(bpm, 1), "conf": round(conf, 2), "rms": round(rms, 4)}
            self._decide(now, playing)
        if self.mode in ("dance", "auto", "asleep"):
            if now >= self.until:
                self.stop("time cap")
            elif self.listen_check_at and now >= self.listen_check_at:
                self.listen_check_at = 0
                r = self.bt.on_beat_ratio(self.gate.bpm())
                if r < _envf("HUB_DANCE_BEAT_RATIO", 1.25):
                    self.stop(f"beat lost (ratio {r:.2f})")
                    self.send_plan("plan:face expr_happy;headdeg 0 400")
            elif now >= self.next_bar:
                self._bar(now)
        if self.mode == "hum" and now >= self.until:
            self.mode = ""
            notes = music.hum_notes(bytes(self.hum_buf))
            self.log(f"music hum {len(self.hum_buf) / 32000:.1f}s -> {len(notes)} notes")
            if sum(d for h, d in notes if h > 0) < 0.3:
                self.say("hum_none", self.lang)
            else:
                import animals
                y = animals.vectorize16(music.beeps(notes))
                self.send_plan("plan:face expr_happy;headdeg 6 300;wait 600;headdeg 0 500")
                self.play((np.clip(y, -1, 1) * 30000).astype("<i2").tobytes())

    def _decide(self, now: float, playing: bool) -> None:
        if self.mode == "listen":
            if playing or self.gate.playing(6):  # asked to dance: 3 s of steady beat is enough
                self._begin("dance")
            elif now >= self.until:
                self.mode = ""
                self.send_plan("plan:face expr_sad;headdeg 0 400")
                self.say("dance_nobeat", self.lang)
            return
        if self.mode or not playing or now < self.cool_until or not self.auto:
            return
        if self.awake():
            self.wheels = False
            self._begin("auto")
        elif _on("HUB_MUSIC_ASLEEP_BOB", "1"):
            self._begin("asleep")

    def run(self) -> None:
        while not self._stop:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - never kill the thread
                self.log(f"music tick failed {exc!r}")
            time.sleep(0.05)
