"""STT latency bench on recorded robot clips (run inside the hub container).

usage: python stt_bench.py clip.wav [...]   (16 kHz mono s16 WAV)
Prints ms per variant; the key is read from the environment and never printed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import wave

from api import Api
from voice import _wav_wrap, multipart, trim_silence

KEY = os.environ["OPENAI_API_KEY"]


def load(p: str) -> bytes:
    with wave.open(p) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.readframes(w.getnframes())


def fresh(body: bytes, ctype: str) -> bytes:
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": ctype},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main() -> None:
    api = Api(KEY)
    variants = [
        ("A mini fresh full auto", "gpt-4o-mini-transcribe", False, False, ""),
        ("B mini keepalive full auto", "gpt-4o-mini-transcribe", True, False, ""),
        ("C mini keepalive trim en", "gpt-4o-mini-transcribe", True, True, "en"),
        ("D whisper-1 keepalive trim en", "whisper-1", True, True, "en"),
        ("E 4o keepalive trim en", "gpt-4o-transcribe", True, True, "en"),
    ]
    clips = [(os.path.basename(p), load(p)) for p in sys.argv[1:]]
    api.post("/v1/audio/transcriptions", *multipart({"model": "whisper-1"}, _wav_wrap(clips[0][1][:16000])), 30)
    for name, model, keep, trim, lang in variants:
        times = []
        for rep in range(2):
            for cname, pcm in clips:
                clip = trim_silence(pcm) if trim else pcm
                body, ctype = multipart({"model": model, "language": lang}, _wav_wrap(clip))
                t = time.time()
                raw = api.post("/v1/audio/transcriptions", body, ctype, 30) if keep else fresh(body, ctype)
                ms = int((time.time() - t) * 1000)
                times.append(ms)
                if rep == 0:
                    print(f"  {name:32s} {cname:14s} {len(pcm)/32000:.1f}s->{len(clip)/32000:.1f}s {ms:5d}ms {json.loads(raw).get('text','')!r}")
        times.sort()
        print(f"{name:32s} median={times[len(times)//2]}ms min={times[0]} max={times[-1]}")


if __name__ == "__main__":
    main()
