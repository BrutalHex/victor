#!/usr/bin/env python3
"""Replay a 16 kHz mono WAV (POST /record) through the old and the new VAD.

    python3 vad_replay.py /app/data/rec/noise-a.wav [...]

Counts the turns each would open (no STT, no network). "old" is the VAD
before 2026-10-10: 5 frames over max(120, 3 x EMA floor) opened a turn.
"""
from __future__ import annotations

import sys
import wave

import voice
from voice import CAL_FRAMES, END_FRAMES, VAD_RATIO, VAD_RMS, Voice, rms, speech_like

FRAME = 640  # 20 ms


class OldVAD:
    def __init__(self):
        self.noise, self.cal, self.voiced, self.silence, self.active, self.n = 200.0, 0, 0, 0, False, 0
        self.buf = 0

    def push(self, pcm):
        e = rms(pcm)
        if self.cal < CAL_FRAMES:
            self.cal += 1
            self.noise = 0.9 * self.noise + 0.1 * e
            return False
        th = max(VAD_RMS, self.noise * VAD_RATIO)
        if e >= th:
            self.voiced += 1
            self.silence = 0
            if not self.active and self.voiced >= 5:
                self.active, self.buf = True, 10
            elif self.active:
                self.buf += 1
        else:
            self.voiced = 0
            if not self.active:
                self.noise = 0.97 * self.noise + 0.03 * e
            else:
                self.silence += 1
                self.buf += 1
                if self.silence >= END_FRAMES:
                    self.active, self.silence = False, 0
                    return self.buf * FRAME >= voice.MIN_UTTERANCE_BYTES
        return False


def replay(path: str) -> dict:
    with wave.open(path, "rb") as w:
        pcm = w.readframes(w.getnframes())
    old, new = OldVAD(), Voice()
    new.key = ""
    o = n = 0
    for i in range(0, len(pcm) - FRAME + 1, FRAME):
        f = pcm[i:i + FRAME]
        if old.push(f):
            o += 1
        got = new.push(f)
        if got and len(got) >= voice.MIN_UTTERANCE_BYTES and speech_like(got):
            n += 1
            if VERBOSE:
                u = new.utt
                print(f"  turn @{i / 32000:6.1f}s len={len(got) / 32000:.2f}s speech_frames={u['speech_frames']} "
                      f"voiced={u['voiced']} run={u['voiced_run']} snr={u.get('snr')} speech_rms={u['speech_rms']} floor={u['floor']:.0f}")
                if STT:
                    stt = Voice()
                    stt.noise = 0.0
                    print(f"    stt: {stt.transcribe(got)!r} {stt.last_drop}")
            new.thinking = False
    secs = len(pcm) / 32000
    return {"file": path, "secs": round(secs, 1), "old_turns": o, "new_turns": n, "floor": round(new.noise),
            "rejected": new.rejected}


VERBOSE = False
STT = False  # --stt: transcribe each accepted turn with the hub key (diagnostics only)

if __name__ == "__main__":
    if "-v" in sys.argv:
        VERBOSE = True
        sys.argv.remove("-v")
    if "--stt" in sys.argv:
        STT = True
        sys.argv.remove("--stt")
    for p in sys.argv[1:]:
        r = replay(p)
        per_min = lambda k: round(r[k] * 60 / max(r["secs"], 1), 1)
        print(f"{r['file']}: {r['secs']} s  old={r['old_turns']} ({per_min('old_turns')}/min)  "
              f"new={r['new_turns']} ({per_min('new_turns')}/min)  floor={r['floor']} rejected={r['rejected']}")
