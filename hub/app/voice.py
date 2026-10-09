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

from api import Api, ApiError
import lang as langmod

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


FRAME_BYTES = int(RATE * 0.02) * 2


def trim_silence(pcm: bytes, lead_ms: int = 160, tail_ms: int = 240) -> bytes:
    """Cut the VAD pre-roll/hang-over silence (up to ~0.9 s) before STT.

    Frames count as speech at max(VAD_RMS, 12% of the loudest frame); keep
    lead_ms before the first and tail_ms after the last speech frame."""
    n = len(pcm) // FRAME_BYTES
    if n < 3:
        return pcm
    levels = [rms(pcm[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]) for i in range(n)]
    thr = max(VAD_RMS, int(0.12 * max(levels)))
    voiced = [i for i, v in enumerate(levels) if v >= thr]
    if not voiced:
        return pcm
    a = max(0, voiced[0] - lead_ms // 20)
    b = min(n, voiced[-1] + 1 + tail_ms // 20)
    out = pcm[a * FRAME_BYTES:b * FRAME_BYTES]
    return out if len(out) >= int(RATE * 0.3) * 2 else pcm


def multipart(fields: dict[str, str], wav: bytes, boundary: str = "----victor") -> tuple[bytes, str]:
    parts = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, v in fields.items()
        if v
    )
    body = (
        parts
        + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
        f"Content-Type: audio/wav\r\n\r\n".encode()
        + wav
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _http_error(where: str, exc: BaseException) -> None:
    body = ""
    if isinstance(exc, (urllib.error.HTTPError, ApiError)):
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


PERSON_CONTEXT = None  # main sets a callable -> camera/identity section (face_id.person_context)


def system_prompt(now: _dt.datetime | None = None) -> str:
    text = SYSTEM_PROMPT + " " + langmod.reply_rule(langmod.allowed()) + "\n" + now_context(now)
    if PERSON_CONTEXT is not None:
        try:
            text += "\n" + PERSON_CONTEXT()
        except Exception as exc:  # noqa: BLE001 - never break a voice turn
            print(f"voice person context failed {exc!r}", flush=True)
    return text


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


VECTOR_STYLE = (
    "You are Vector, a small cheerful desk robot. Speak clearly in a bright, friendly, "
    "slightly clipped and evenly paced voice with a hint of robotic precision; curious and "
    "upbeat, never breathy or whispery. Medium-slow pace. Speak the text in its own language "
    "(English, German or Persian) with native pronunciation."
)


def vector_voice(raw24k: bytes) -> bytes:
    """OpenAI 24 kHz PCM -> Vector-style 16 kHz PCM. Falls back to a plain
    resample if numpy/scipy are missing (HUB_VOICE_FX only needs them)."""
    try:
        import voicefx
    except ImportError:
        return pcm16k(raw24k, TTS_RATE)
    pcm = voicefx.pcm_vectorize(raw24k, TTS_RATE) if _env_on("HUB_VOICE_VECTOR", "1") else raw24k
    return voicefx.resample(pcm, TTS_RATE, RATE)


def warm_fx() -> float:
    """Run the chain once on 0.3 s of silence so the first real reply does not
    pay scipy's import/filter-design cost (measured ~2 s cold on the laptop).
    Returns ms spent, or -1 when the chain is unavailable."""
    t = time.perf_counter()
    try:
        vector_voice(bytes(int(TTS_RATE * 0.3) * 2))
    except Exception:
        return -1.0
    return (time.perf_counter() - t) * 1000


REPLY_MAX_CHARS = int(os.environ.get("HUB_REPLY_MAX_CHARS", "300"))
MAX_SPEAK_S = 55  # the robot link drops commands over 2 MiB (~65 s of 16 kHz PCM)


def short_reply(text: str, limit: int = 0) -> str:
    """Keep whole sentences up to limit chars. Web search sometimes returns a
    whole forecast table (1,500+ chars, 90 s of speech) despite the prompt."""
    limit = limit or REPLY_MAX_CHARS
    if len(text) <= limit:
        return text
    out = ""
    for sent in re.split(r"(?<=[.!?؟۔])\s+", text):
        if out and len(out) + 1 + len(sent) > limit:
            break
        out = f"{out} {sent}".strip()
        if len(out) >= limit:
            break
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return out


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
        # Spoken languages (HUB_LANGS, default en,de,fa). STT auto-detects with a
        # prompt naming them; the transcript is then checked (lang.classify) and
        # anything else is retried once or dropped. HUB_STT_LANGUAGE pins one.
        self.langs = langmod.allowed()
        self.stt_language = os.environ.get("HUB_STT_LANGUAGE", "").strip()
        self.lang_retry = _env_on("HUB_LANG_RETRY", "1")
        self.drop_ratio = float(os.environ.get("HUB_DROP_RMS_RATIO", "1.2"))
        self.last_lang = ""
        self.last_drop = ""
        self.stt_trim = _env_on("HUB_STT_TRIM", "1")
        self.api = Api(self.key)
        self.last_stt: dict = {}
        self.tts_model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        # Vector imitation: OpenAI voice + voicefx chain (pitch/formant up,
        # comb, small-speaker EQ). HUB_VOICE picks the OpenAI voice (the old
        # OPENAI_VOICE is ignored so an existing .env can't undo the default).
        self.voice = os.environ.get("HUB_VOICE", "").strip() or "echo"
        self.tts_instructions = os.environ.get("HUB_VOICE_INSTRUCTIONS", VECTOR_STYLE)
        self.last_tts_raw = b""
        self.last_fx_ms = 0
        if True:  # resample uses scipy even with HUB_VOICE_VECTOR=0 (~1.7 s cold)
            import threading
            threading.Thread(target=lambda: print(f"tts fx warm ms={warm_fx():.0f}", flush=True), daemon=True).start()
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
                if self.key:
                    self.api.warm()  # TLS while the user is still talking
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
        """STT limited to HUB_LANGS. Returns "" for a dropped turn (noise or a
        language we never speak); last_lang / last_drop say what happened."""
        self.last_lang, self.last_drop = "", ""
        if not self.key:
            print("voice transcribe skipped; no key", flush=True)
            return ""
        clip = trim_silence(pcm) if self.stt_trim else pcm
        level, floor = rms(clip), float(getattr(self, "noise", 0.0))
        secs = len(clip) / (2 * RATE)
        if floor > 0 and level < self.drop_ratio * floor:
            self.last_drop = f"quiet rms={level} floor={floor:.0f}"
            print(f"voice drop {self.last_drop}", flush=True)
            return ""
        text = self._stt(pcm, clip, self.stt_language)
        if not text:
            return ""
        lang, why = langmod.classify(text, self.langs)
        if not lang:
            loud = floor <= 0 or level >= 2 * floor
            if self.lang_retry and not self.stt_language and loud and secs >= 1.0 and len(text.strip()) >= 4:
                force = langmod.retry_language(why, self.langs)
                print(f"voice lang reject {why} {text[:80]!r}; retry language={force}", flush=True)
                text2 = self._stt(pcm, clip, force)
                lang, why2 = langmod.classify(text2, self.langs) if text2 else ("", "empty")
                if lang:
                    text = text2
                else:
                    why = f"{why}/{why2}"
            if not lang:
                self.last_drop = f"lang {why} {text[:80]!r}"
                print(f"voice drop {self.last_drop} rms={level} floor={floor:.0f} s={secs:.2f}", flush=True)
                return ""
        self.last_lang = lang
        print(f"voice lang={lang} ({why})", flush=True)
        return text

    def _stt(self, pcm: bytes, clip: bytes, language: str) -> str:
        t0 = time.time()
        fields = {"model": self.stt_model, "language": language, "response_format": "json"}
        if not language:
            fields["prompt"] = langmod.stt_prompt(self.langs)
        body, ctype = multipart(fields, _wav_wrap(clip))
        connects = self.api.connects
        try:
            raw = self.api.post("/v1/audio/transcriptions", body, ctype, 20).decode()
            data = json.loads(raw)
            text = str(data.get("text") or "").strip()
            if not text:
                print(f"voice transcribe empty {raw[:240]}", flush=True)
            return text
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            _http_error("transcribe", exc)
            return ""
        finally:
            self.last_stt = {
                "ms": int((time.time() - t0) * 1000),
                "audio_s": round(len(pcm) / (2 * RATE), 2),
                "sent_s": round(len(clip) / (2 * RATE), 2),
                "bytes": len(body),
                "new_tls": self.api.connects - connects,
                "model": self.stt_model,
                "language": language or "auto",
            }
            print("stt " + " ".join(f"{k}={v}" for k, v in self.last_stt.items()), flush=True)

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
                self.last_via = "responses"
        if not reply:
            reply = self._chat_plain(text)
            if reply:
                self.last_via = "chat"
        self.last_chat_ms = int((time.monotonic() - t0) * 1000)
        full = speakable(reply)
        reply = short_reply(full)
        if len(reply) < len(full):
            print(f"voice reply cut {len(full)} -> {len(reply)} chars", flush=True)
        print(f"voice chat via={self.last_via or 'none'} searched={self.last_searched} ms={self.last_chat_ms}", flush=True)
        return reply

    def _post(self, url: str, payload: dict, timeout: float) -> dict:
        path = url.split("api.openai.com", 1)[-1]
        raw = self.api.post(path, json.dumps(payload).encode(), "application/json", timeout)
        return json.loads(raw.decode())

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
        body = {"model": self.tts_model, "voice": self.voice, "input": text, "response_format": "pcm"}
        if self.tts_instructions and self.tts_model.startswith("gpt-4o"):
            body["instructions"] = self.tts_instructions
        try:
            raw = self.api.post("/v1/audio/speech", json.dumps(body).encode(), "application/json", 30)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _http_error("tts", exc)
            return b""
        self.last_tts_raw = raw
        t = time.time()
        pcm = vector_voice(raw)
        self.last_fx_ms = int((time.time() - t) * 1000)
        print(f"tts fx ms={self.last_fx_ms} in_s={len(raw) / (2 * TTS_RATE):.2f} out_s={len(pcm) / (2 * RATE):.2f}", flush=True)
        return pcm[: MAX_SPEAK_S * RATE * 2]


def _wav_wrap(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
