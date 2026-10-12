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
        self.intended = 0.0
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
        if was in ("dance", "auto", "asleep"):
            self.send_plan("plan:keepbase;headdeg 0 400")  # back to where the dance started
        if was:
            self.log(f"music stop {was}: {why}")
        return bool(was)

    # -- internals
    def _begin(self, mode: str) -> None:
        self.mode, self.bar_n = mode, 0
        self.until = self.clock() + _envf("HUB_DANCE_MAX_S", 90.0 if mode == "dance" else 45.0)
        self.next_bar = 0.0
        self.intended = 0.0
        self.bpm_lock = self.gate.bpm()
        self.start_rms = self.bt.level(4.0)
        self.log(f"music {mode} start bpm={self.gate.bpm():.1f}")
        self.send_plan(f"plan:headdeg {music.POSE_DEG} 300")  # dancing pose; its start is the base

    def _rms(self, secs: float = 4.0, mean: bool = False) -> float:
        r = self.bt.rms[-int(secs * music.FPS):]
        if not len(r):
            return 0.0
        return float(np.sqrt((r ** 2).mean())) if mean else float(np.median(r))

    def _bar(self, now: float) -> None:
        bpm = getattr(self, "bpm_lock", 0.0) or self.gate.bpm() or self.bt.tempo()[0]
        g = self.gate.bpm()
        if g and abs(g - bpm) / bpm < 0.04:  # follow small drifts, ignore jumps (motor noise)
            bpm = self.bpm_lock = g
        _, beats = self.bt.next_beats()
        if not beats or bpm <= 0:
            return
        # beats are relative to the end of the tracked audio (its arrival
        # time). Each beat is its own plan whose first step (the accent) must
        # END on the beat: sent LEAD (audio + command latency, measured) +
        # ACCENT_S early.
        per = music.beat_period(bpm)
        lead = _envf("HUB_DANCE_LEAD_MS", 20) / 1000 + music.ACCENT_S
        ref = self.audio_end or now
        beat = 60.0 / bpm
        start = ref + beats[0] - lead
        while start < now + 0.02:
            start += beat
        if self.bar_n and self.intended:
            # keep bars back to back: the grid beat nearest to where this bar
            # should start (prev start + its length), never in the past
            # phase locked too: only nudge (<=30 ms) toward the tracker, whose
            # phase jumped +-70 ms between bars under motor noise (measured)
            k = round((self.intended - start) / beat)
            cand = start + k * beat
            err = cand - self.intended if abs(cand - self.intended) <= beat / 2 + 1e-6 else 0.0
            start = max(now + 0.01, self.intended + max(-0.03, min(0.03, err * 0.5)))
        listen = self.bar_n % 4 == 3
        self._beat_s = per
        plans = music.dance_beats(bpm, on_charger=self.on_charger(), wheels=self.wheels and self.mode == "dance",
                                  head_only=self.mode == "asleep", rng=self.rng, listen=listen)
        mode = self.mode
        for k, plan in enumerate(plans):
            delay = max(0.0, start + k * per - now)
            self.schedule(delay, (lambda p=plan: self._send_if(mode, p)))
        dur = per * len(plans)
        self.next_bar = start + dur - 0.15  # plan the next bar before its send time
        self.intended = start + dur
        self.bar_n += 1
        self.listen_check_at = start + dur - 0.3 if listen else 0.0  # before the next bar is planned

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
            rms = self._rms()
            playing = self.gate.update(bpm, conf, rms)
            self.last_eval = {"bpm_now": round(bpm, 1), "conf": round(conf, 2), "rms": round(rms, 4)}
            self._decide(now, playing)
        if self.mode in ("dance", "auto", "asleep"):
            if now >= self.until:
                self.stop("time cap")
            elif self.bt.level(2.0) < 0.2 * getattr(self, "start_rms", 0.0):
                # level dropped (song over); motor noise only ever raises it
                self.stop(f"music stopped (level {self.bt.level(2.0):.4f})")
                self.send_plan("plan:keepbase;face expr_happy;headdeg 0 400")
            elif self.listen_check_at and now >= self.listen_check_at:
                self.listen_check_at = 0
                r = self.bt.on_beat_ratio(self.bpm_lock or self.gate.bpm())
                if r < _envf("HUB_DANCE_BEAT_RATIO", 1.25):
                    self.stop(f"beat lost (ratio {r:.2f})")
                    self.send_plan("plan:keepbase;face expr_happy;headdeg 0 400")
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
