"""VAD → thinking bar → OpenAI STT → OPENAI_MODEL → TTS PCM. Key stays on the hub.

push() only segments audio. Transcription, chat, and TTS run off the skill socket
so a 2s robot read deadline cannot drop the reply.

The robot sends one backpack mic at 16 kHz (high-passed, levelled toward
~2500 rms). A rumble-dominated clip is not an utterance: the 4 cm array cannot
null a 200 Hz fan, and the transcriber invents words for that band.

VAD levels are pre-emphasised rms (rms() below). Measured on the robot after
the spine offset fix (2026-10-09, quiet room, robot on charger): silence ~35,
speech median ~400 at the robot's levelled gain. The old 450 floor was tuned
against byte-misaligned mic data that read as ±1000 hiss and never let a real
voice through.
"""

from __future__ import annotations

import io
import json
import math
import os
import struct
import urllib.error
import urllib.request
import wave

RATE = 16000
TTS_RATE = 24000  # OpenAI response_format=pcm
VAD_RMS = int(os.environ.get("HUB_VAD_RMS", "120"))
VAD_RATIO = float(os.environ.get("HUB_VAD_RATIO", "3.0"))
START_FRAMES = 5
END_FRAMES = 8
CAL_FRAMES = 50  # 1s at 20 ms packets; learn the room before arming
MIN_UTTERANCE_BYTES = int(RATE * 0.4) * 2
MAX_SAMPLES = RATE * 6


def speech_like(pcm: bytes) -> bool:
    """False only for a hard-clipped sub-200 Hz band.

    A laptop recording in this room peaks at 5845 and sits in 300-800 Hz.
    The previous gate called that shape rumble and dropped the sentence.
    """
    if len(pcm) < 640:
        return False
    n = len(pcm) // 2
    samples = [struct.unpack_from("<h", pcm, i * 2)[0] for i in range(n)]
    peak = max(abs(s) for s in samples)
    if peak < 20000:
        return True
    low = 0.0
    mid = 0.0
    rumble = 0.0
    a = 0.075
    for s in samples:
        low += a * (float(s) - low)
        d = float(s) - low
        mid += d * d
        rumble += low * low
    if rumble > mid * 4:
        return False
    return True


def rms(pcm: bytes) -> int:
    if len(pcm) < 4:
        return 0
    n = len(pcm) // 2
    samples = [struct.unpack_from("<h", pcm, i * 2)[0] for i in range(n)]
    # Pre-emphasis. Track rumble that survived the robot high-pass should not set the floor.
    prev = 0.0
    emph = []
    for s in samples:
        y = s - 0.97 * prev
        prev = float(s)
        emph.append(y)
    mean = sum(emph) / n
    acc = 0.0
    for y in emph:
        d = y - mean
        acc += d * d
    return int(math.sqrt(acc / n))


def tone(hz: float = 440.0, ms: int = 300, rate: int = RATE) -> bytes:
    n = rate * ms // 1000
    buf = bytearray()
    for i in range(n):
        s = int(8000 * math.sin(2 * math.pi * hz * i / rate))
        buf += struct.pack("<h", s)
    return bytes(buf)


def pcm16k(pcm: bytes, src_rate: int = TTS_RATE) -> bytes:
    """Linear resample s16le mono to the robot speaker rate (16 kHz)."""
    if not pcm or src_rate == RATE:
        return pcm
    n = len(pcm) // 2
    if n < 2:
        return pcm[: n * 2]
    samples = struct.unpack("<" + "h" * n, pcm[: n * 2])
    out_n = int(n * RATE / src_rate)
    if out_n < 1:
        return b""
    out = []
    last = n - 1
    for i in range(out_n):
        x = i * src_rate / RATE
        j = int(x)
        if j >= last:
            out.append(samples[last])
            continue
        frac = x - j
        a = samples[j]
        b = samples[j + 1]
        out.append(int(a + (b - a) * frac))
    return struct.pack("<" + "h" * len(out), *out)


def _http_error(where: str, exc: BaseException) -> None:
    body = ""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode(errors="replace")[:300]
        except OSError:
            body = ""
    print(f"voice {where} error {exc} {body}", flush=True)


class Voice:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.voiced = 0
        self.silence = 0
        self.active = False
        self.thinking = False
        self.last_text = ""
        self.last_reply = ""
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.stt_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
        self.tts_model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.voice = os.environ.get("OPENAI_VOICE", "alloy")
        self.last_rms = 0
        self.noise = 200.0
        self.cal_frames = 0

    def push(self, pcm: bytes) -> bytes | None:
        """Return captured PCM when an utterance closes. Does not call OpenAI."""
        if not pcm:
            return None
        energy = rms(pcm)
        self.last_rms = energy
        if self.cal_frames < CAL_FRAMES:
            self.cal_frames += 1
            self.noise = (0.90 * self.noise) + (0.10 * float(energy))
            return None
        thresh = max(VAD_RMS, self.noise * VAD_RATIO)
        if energy >= thresh:
            self.voiced += 1
            self.silence = 0
            if not self.active and self.voiced >= START_FRAMES:
                self.active = True
                self.buf = bytearray()
            if self.active:
                self.buf += pcm
        else:
            self.voiced = 0
            if not self.active:
                self.noise = (0.97 * self.noise) + (0.03 * float(energy))
            if self.active:
                self.silence += 1
                self.buf += pcm
                if self.silence >= END_FRAMES:
                    return self._take()
        if self.active and len(self.buf) >= MAX_SAMPLES * 2:
            return self._take()
        return None

    def _take(self) -> bytes:
        pcm = bytes(self.buf)
        self.buf.clear()
        self.active = False
        self.silence = 0
        self.voiced = 0
        self.thinking = True
        return pcm

    def transcribe(self, pcm: bytes) -> str:
        if not self.key:
            print("voice transcribe skipped; no key", flush=True)
            return ""
        wav = _wav_wrap(pcm)
        boundary = "----victor"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{self.stt_model}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + wav + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode()
            data = json.loads(raw)
            text = str(data.get("text") or "").strip()
            if not text:
                print(f"voice transcribe empty {raw[:240]}", flush=True)
            return text
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            _http_error("transcribe", exc)
            return ""

    def chat(self, text: str) -> str:
        if not self.key or not text:
            return ""
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are Vector, a small desk robot. Replies under 12 words. No markdown.",
                    },
                    {"role": "user", "content": text},
                ],
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, IndexError) as exc:
            _http_error("chat", exc)
            return ""

    def tts(self, text: str) -> bytes:
        if not text:
            return b""
        if not self.key:
            return tone()
        payload = json.dumps(
            {"model": self.tts_model, "voice": self.voice, "input": text, "response_format": "pcm"}
        ).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech",
            data=payload,
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return pcm16k(resp.read(), TTS_RATE)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _http_error("tts", exc)
            return b""


def _wav_wrap(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
