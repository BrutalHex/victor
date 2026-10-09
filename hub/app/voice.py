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

import datetime as _dt
import io
import json
import math
import os
import re
import struct
import time
import urllib.error
import urllib.request
import wave
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RATE = 16000
TTS_RATE = 24000  # OpenAI response_format=pcm
VAD_RMS = int(os.environ.get("HUB_VAD_RMS", "120"))
VAD_RATIO = float(os.environ.get("HUB_VAD_RATIO", "3.0"))
START_FRAMES = 5
# 700 ms of quiet closes a turn. 160 ms split "Hello Vector, what time is it?"
# at the comma and sent only "Hello, Victor." to the model.
END_FRAMES = int(os.environ.get("HUB_VAD_END_FRAMES", "35"))
PREROLL_FRAMES = 10  # 200 ms kept from before the onset so the first word is whole
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


SYSTEM_PROMPT = (
    "You are Vector, a small desk robot that answers out loud. "
    "Reply in one or two short spoken sentences, under 30 words. "
    "Plain text only: no markdown, no lists, no URLs, no citations or source names in brackets. "
    "For today's date, the weekday or the time, use the local clock below; never say you cannot know it. "
    "For anything current (weather, news, scores, prices, opening hours, recent events) use web search "
    "when it is available, then answer with the key fact."
)


def hub_tz() -> tuple[_dt.tzinfo, str]:
    name = (os.environ.get("HUB_TZ") or "Europe/Berlin").strip()
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError):
        print(f"voice HUB_TZ {name!r} unknown; using UTC", flush=True)
        return _dt.timezone.utc, "UTC"


def now_context(now: _dt.datetime | None = None) -> str:
    """Local clock for the model, computed per turn. now is aware or UTC-naive."""
    tz, name = hub_tz()
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    local = now.astimezone(tz)
    off = local.strftime("%z")
    off = f"UTC{off[:3]}:{off[3:]}" if off else "UTC"
    text = (
        f"Local clock: {local.strftime('%A')}, {local.day} {local.strftime('%B %Y')}, "
        f"{local.strftime('%H:%M')} ({name}, {off}). ISO date {local.date().isoformat()}."
    )
    city = os.environ.get("HUB_CITY", "").strip()
    country = os.environ.get("HUB_COUNTRY", "").strip()
    if city or country:
        text += " The robot is in " + ", ".join(x for x in (city, country) if x) + "."
    return text


def system_prompt(now: _dt.datetime | None = None) -> str:
    return SYSTEM_PROMPT + "\n" + now_context(now)


_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:https?://|www\.)[^)]*\)")
_PAREN_SRC = re.compile(r"\(\s*(?:\[[^\]]*\]\([^)]*\)[\s,;]*)+\)")
_URL = re.compile(r"(?:https?://|www\.)\S+")
_BRACKET_CITE = re.compile(r"【[^】]*】|\[\d+(?:,\s*\d+)*\]")
_EMPTY_PAREN = re.compile(r"\(\s*[,;]?\s*\)")


def speakable(text: str) -> str:
    """Strip citations, URLs and markdown so TTS reads only the answer."""
    if not text:
        return ""
    t = _PAREN_SRC.sub("", text)
    t = _MD_LINK.sub(r"\1", t)
    t = _BRACKET_CITE.sub("", t)
    t = _URL.sub("", t)
    t = re.sub(r"\*\*|__|`|^#+\s*|^\s*[-*•]\s+", "", t, flags=re.M)
    t = _EMPTY_PAREN.sub("", t)
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _env_on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def _responses_text(data: dict) -> tuple[str, bool]:
    """Pull output text and whether a web_search_call ran from a Responses API body."""
    searched = False
    parts: list[str] = []
    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "web_search_call":
            searched = True
        elif kind == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text"):
                    parts.append(c["text"])
    if not parts and isinstance(data.get("output_text"), str):
        parts.append(data["output_text"])
    return " ".join(parts).strip(), searched


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
        # Live web via the Responses API web_search tool (gpt-4o-mini supports it).
        self.web_search = _env_on("HUB_WEB_SEARCH", "1")
        self.search_model = os.environ.get("HUB_SEARCH_MODEL", "") or self.model
        self.search_timeout = float(os.environ.get("HUB_SEARCH_TIMEOUT", "15"))
        self.last_searched = False
        self.last_via = ""
        self.last_chat_ms = 0
        self.last_rms = 0
        self.noise = 200.0
        self.cal_frames = 0
        self.preroll: list[bytes] = []

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
        if not self.active:
            self.preroll.append(pcm)
            if len(self.preroll) > PREROLL_FRAMES:
                self.preroll.pop(0)
        if energy >= thresh:
            self.voiced += 1
            self.silence = 0
            if not self.active and self.voiced >= START_FRAMES:
                self.active = True
                self.buf = bytearray(b"".join(self.preroll))
                self.preroll = []
            elif self.active:
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
        """One spoken reply. Web search (Responses API) first, plain chat as fallback."""
        if not self.key or not text:
            return ""
        t0 = time.monotonic()
        self.last_searched = False
        self.last_via = ""
        reply = ""
        if self.web_search:
            reply, self.last_searched = self._chat_web(text)
            if reply:
                self.last_via = "responses+web_search"
        if not reply:
            reply = self._chat_plain(text)
            if reply:
                self.last_via = "chat"
        self.last_chat_ms = int((time.monotonic() - t0) * 1000)
        reply = speakable(reply)
        print(f"voice chat via={self.last_via or 'none'} searched={self.last_searched} ms={self.last_chat_ms}", flush=True)
        return reply

    def _post(self, url: str, payload: dict, timeout: float) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())

    def _chat_web(self, text: str) -> tuple[str, bool]:
        tz_name = hub_tz()[1]
        tool: dict = {"type": "web_search", "search_context_size": "low"}
        loc = {"type": "approximate", "timezone": tz_name}
        if os.environ.get("HUB_CITY", "").strip():
            loc["city"] = os.environ["HUB_CITY"].strip()
        if os.environ.get("HUB_COUNTRY", "").strip():
            loc["country"] = os.environ["HUB_COUNTRY"].strip()
        tool["user_location"] = loc
        payload = {
            "model": self.search_model,
            "instructions": system_prompt(),
            "input": text,
            "tools": [tool],
            "tool_choice": "auto",
            "max_output_tokens": 300,
        }
        try:
            data = self._post("https://api.openai.com/v1/responses", payload, self.search_timeout)
            return _responses_text(data)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
            _http_error("web_search", exc)
            return "", False

    def _chat_plain(self, text: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": text},
            ],
        }
        try:
            data = self._post("https://api.openai.com/v1/chat/completions", payload, 20)
            return data["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, IndexError, TypeError) as exc:
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
