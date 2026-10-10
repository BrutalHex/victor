"""Wake phrase ("Hey Vector") and session-stop phrase matching on transcripts.

No local model, no robot-side detector (owner's choice, 10 Oct 2026): the
robot streams the mic as before, the hub noise gate and the normal OpenAI
STT run, and while the session is asleep the transcript is only checked
here. A miss is dropped silently (no router, chat, TTS or thinking face).
"""
from __future__ import annotations

import re
import unicodedata

_FA_MAP = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": " ", "آ": "ا"})

NAME = r"(?:vector|victor|vektor|viktor|wektor|vecter|vectar|vektar|vecktor|wector|ویکتور|وکتور|وکتر|ویکتر)"
GREET = (r"(?:hey|hay|hei|heh|hi|he|ey|eh|a|hello|hallo|halo|hallå|ok|okay|salam|salaam|salom|"
         r"سلام|هی|های|هِی)")
_WAKE_START = re.compile(rf"^(?:(?:oh|so|and|um|uh|well|ja|na|ok|okay|خب)\s+)?(?P<greet>{GREET}\s+)?(?P<name>{NAME})\b(?P<rest>.*)$")
# mid-sentence only after a real greeting ("a vector field", "he Victor said" are not wakes)
GREET_ANY = r"(?:hey|hi|hello|hallo|ok|okay|salam|salaam|سلام|هی)"
_WAKE_ANY = re.compile(rf"(?:^|\s)(?P<greet>{GREET_ANY})\s+(?P<name>{NAME})\b(?P<rest>.*)$")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower().translate(_FA_MAP)
    t = t.replace("’", "'").replace("-", " ")
    t = re.sub(r"[^\w\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def match_wake(text: str, bare: bool = True) -> tuple[bool, str]:
    """(hit, rest). Hits: the name opening the sentence after an optional
    greeting ("Vector, ...", "Hey Victor ...", "Hallo Vektor", "سلام وکتور"),
    or greeting + name anywhere ("so um, hey Vector, what time is it").
    bare=False requires the greeting. rest = what followed (normalised)."""
    t = norm(text)
    m = _WAKE_START.match(t)
    if m and (bare or m.group("greet")):
        pass
    else:
        m = _WAKE_ANY.search(t)
        if not m:
            return False, ""
    rest = m.group("rest").strip()
    rest = re.sub(r"^(?:please|bitte|لطفا)\s*$", "", rest).strip()
    return True, rest


# Raw (OpenAI) transcript: strip a leading wake phrase before routing / chat.
_STRIP = re.compile(
    rf"^\W*(?:(?:oh|so|um|uh|well)\s*[,]?\s+)?(?:{GREET}\s*[,!.]?\s+)?{NAME}\b\s*[,!.:;؛،]*\s*", re.I
)


_STRIP_ANY = re.compile(rf"^.*?(?<!\w){GREET_ANY}\s*[,!.]?\s+{NAME}\b\s*[,!.:;؛،]*\s*", re.I)


def strip_wake(text: str) -> str:
    """Raw transcript minus the wake phrase (and anything before it)."""
    t = unicodedata.normalize("NFC", text or "").translate(_FA_MAP).strip()
    out = _STRIP.sub("", t, count=1)
    if out == t:
        out = _STRIP_ANY.sub("", t, count=1)
    return out.strip()


# Session end. Whole utterance only (fillers allowed): "Vector, stop driving"
# is a motion command, plain "stop" stays the stop intent.
_FW = r"(?:ok|okay|alright|thanks|thank you|please|bitte|danke|now|jetzt|خب|ممنون|مرسی|لطفا|دیگه)"
_STOP_CORE = (
    rf"(?:stop|stopp|end|quit)\s+(?:listening\s+)?{NAME}",
    rf"{NAME}\s+(?:stop|stopp|end|quit)(?:\s+listening)?",
    r"(?:stop|quit|end)\s+listening",
    r"(?:you can |du kannst )?(?:stop listening|aufhören zuzuhören)",
    rf"(?:{NAME}\s+)?hör(?:e)? auf (?:zuzuhören|zu zuhören|zu hören|mir zuzuhören)",
    rf"(?:{NAME}\s+)?nicht mehr zuhören",
    rf"{NAME}\s+(?:دیگه\s+)?(?:بسه|بس کن|استاپ|استپ|توقف|گوش نکن)",
    rf"(?:بسه|استاپ|استپ)\s+{NAME}",
    rf"(?:{NAME}\s+)?(?:دیگه )?گوش نده",
)
_STOP = re.compile(rf"^(?:{_FW}\s+)*(?:{'|'.join(_STOP_CORE)})(?:\s+{_FW})*$")


def is_session_stop(text: str) -> bool:
    t = norm(text)
    if not t or len(t.split()) > 7:
        return False
    return bool(_STOP.match(t))


def stop_lang(text: str, fallback: str = "en") -> str:
    t = norm(text)
    if re.search(r"[\u0600-\u06ff]", t):
        return "fa"
    if re.search(r"\b(?:stopp|hör|höre|zuzuhören|zuhören|vektor|danke|bitte)\b", t):
        return "de"
    if fallback in ("de", "fa") and not re.search(r"\b(?:listening|stop vector|vector stop)\b", t):
        return fallback
    return "en"
