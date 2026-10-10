"""Self-introductions: "my name is X", "I'm X", "ich bin X", "من X هستم" ...

intro_candidate() is a cheap local pre-filter: a transcript that could be an
introduction always goes to the OpenAI router (never the regex fast path), and
the router decides whether it really is one and extracts the name. valid_name()
then rejects what is clearly not a name ("hungry", "müde", "خسته", ...) and
names that do not occur in what was said (no invented names).
"""

from __future__ import annotations

import difflib
import re
import unicodedata

_INTRO = re.compile(
    r"(?:\bmy name(?:'s| is)\b|\bi am\b|\bi'm\b|\bim\b|\bcall me\b|\bthis is\b|"
    r"\bich hei(?:ß|ss)e\b|\bich bin\b|\bmein name ist\b|\bnenn(?:t)? mich\b|"
    r"اسم\s*من|اسمم|من\s+\S+(?:\s+\S+)?\s+(?:هستم|ام)|منم\s+\S+)",
    re.I,
)

# "I am <word>" that is never a name (states, places, fillers), en/de/fa.
NOT_NAMES = {
    # en
    "hungry", "tired", "sleepy", "fine", "ok", "okay", "good", "great", "well", "bad", "sad", "happy", "bored",
    "busy", "back", "home", "here", "there", "ready", "done", "sorry", "sick", "cold", "hot", "late", "early",
    "lost", "confused", "angry", "excited", "sure", "not", "so", "very", "really", "just", "going", "leaving",
    "coming", "trying", "thinking", "talking", "listening", "your", "the", "a", "an", "fine thanks", "awake",
    "alive", "human", "a human", "a person", "nobody", "somebody", "someone", "you", "it", "me", "who", "what",
    "hungry now", "thirsty", "stressed", "worried", "scared", "afraid", "glad", "proud", "alone", "free",
    "working", "eating", "sleeping", "playing", "vector", "victor", "robot", "a robot", "your owner", "your friend",
    "your father", "your dad", "your mom", "your mother", "fat", "old", "young", "tall", "short", "right", "wrong",
    # de
    "müde", "hungrig", "fertig", "hier", "da", "zurück", "zuhause", "zu hause", "gut", "schlecht", "traurig",
    "glücklich", "sauer", "krank", "bereit", "wach", "durstig", "gestresst", "beschäftigt", "allein", "nicht",
    "so", "sehr", "ein mensch", "dein freund", "dein besitzer", "du", "wer", "was", "es", "satt",
    # fa
    "خسته", "گرسنه", "گشنه", "خوب", "بد", "ناراحت", "خوشحال", "اینجا", "خونه", "خانه", "آماده", "مریض",
    "بیدار", "تشنه", "تنها", "کی", "چی", "کجا", "صاحبت", "دوستت", "آدم", "خوبم", "خستم", "خسته‌ام",
}

_DROP = re.compile(r"^(?:hey|hi|hello|hallo|salam|سلام)\s*,?\s*(?:vector|victor|vektor|viktor|وکتور)?\s*[,.!]?\s*", re.I)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def intro_candidate(text: str) -> bool:
    """Could this be someone telling their name? (then ask the router)"""
    t = (text or "").strip()
    if not t or len(t.split()) > 14:
        return False
    if re.search(r"(?:who am i|wer bin ich|wie hei(?:ß|ss)e ich|what(?:'s| is) my name|من\s*کی\s*هستم|اسمم?\s*(?:من\s*)?چیه)", t, re.I):
        return False
    return bool(_INTRO.search(t))


def valid_name(name: str, text: str = "") -> str:
    """Cleaned name or "" if it is not a plausible personal name for this utterance."""
    n = re.sub(r"[\x00-\x1f\x7f<>{}\[\]\"`.,!?؟;:()]", " ", str(name or ""))
    n = re.sub(r"\s+", " ", n).strip(" -'")
    if not n or len(n) > 40 or len(n.split()) > 3:
        return ""
    if any(ch.isdigit() for ch in n):
        return ""
    if not all(ch.isalpha() or ch in " -'‌" for ch in n):
        return ""
    if _fold(n) in {_fold(x) for x in NOT_NAMES} or n.lower().split()[0] in ("a", "an", "the", "not", "so", "very", "ein", "eine", "nicht"):
        return ""
    if text and n.isascii() and _latin_share(text) > 0.6:
        # the name must be in what was said (STT spelling may differ a little)
        words = re.findall(r"[^\W\d_]+", _fold(text))
        for part in [p for p in re.split(r"[\s\-']+", _fold(n)) if p]:
            if part not in words and not difflib.get_close_matches(part, words, n=1, cutoff=0.8):
                return ""
    if n.isascii():
        n = " ".join(w[:1].upper() + w[1:] for w in n.split())
    return n


def _latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isascii()) / len(letters)


def canonical(name: str, existing: list[str]) -> str:
    """Reuse the stored spelling when the name is already enrolled (case/accents ignored)."""
    f = _fold(name)
    for e in existing:
        if _fold(e) == f:
            return e
    return name


def strip_wake(text: str) -> str:
    return _DROP.sub("", text or "").strip()
