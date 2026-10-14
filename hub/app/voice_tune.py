"""Tune the Vector voice: synthesise one line per OpenAI voice, report raw and
processed F0 / spectral centroid and fx time, write WAVs. Run in the hub
container: python voice_tune.py /app/data/tune [voice ...]"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

import voicefx
from voice import TTS_RATE, VECTOR_STYLE, Voice, _wav_wrap

LINE = "Hi, I'm Vector. It's seventeen fifty in Berlin, and I'm feeling great!"


def main() -> None:
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    voices = sys.argv[2:] or ["echo", "ash", "verse", "cedar", "alloy"]
    v = Voice()
    for name in voices:
        body = {"model": v.tts_model, "voice": name, "input": LINE, "response_format": "pcm", "instructions": VECTOR_STYLE}
        raw = v.api.post("/v1/audio/speech", json.dumps(body).encode(), "application/json", 30)
        x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
        t = time.time()
        y = voicefx.vectorize(x, TTS_RATE)
        ms = (time.time() - t) * 1000
        print(f"{name:7s} raw F0={voicefx.f0_median(x, TTS_RATE):6.1f} Hz centroid={voicefx.centroid(x, TTS_RATE):6.0f} Hz | "
              f"vector F0={voicefx.f0_median(y, TTS_RATE):6.1f} Hz centroid={voicefx.centroid(y, TTS_RATE):6.0f} Hz | "
              f"{len(x)/TTS_RATE:.2f}s -> {len(y)/TTS_RATE:.2f}s fx {ms:.0f} ms", flush=True)
        open(f"{out}/{name}-raw.wav", "wb").write(_wav_wrap(raw, TTS_RATE))
        open(f"{out}/{name}-vector.wav", "wb").write(_wav_wrap((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes(), TTS_RATE))


if __name__ == "__main__":
    main()
