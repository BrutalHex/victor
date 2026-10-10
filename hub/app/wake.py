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

NAME = r"(?:vector|victor|vektor|viktor|wektor|vecter|vectar|vektar|vecktor|wector|ویکتور|وکتور|وکتر|ویکتر|вектор|виктор)"
GREET = (r"(?:hey|hay|hei|hej|heh|hi|he|ey|eh|a|hello|hallo|halo|hallå|ok|okay|salam|salaam|salom|"
         r"سلام|هی|های|هِی|эй|хей|хэй|привет)")
_WAKE_START = re.compile(rf"^(?:(?:oh|so|and|um|uh|well|ja|na|ok|okay|خب)\s+)?(?P<greet>{GREET}\s+)?(?P<name>{NAME})\b(?P<rest>.*)$")
# mid-sentence only after a real greeting ("a vector field", "he Victor said" are not wakes)
GREET_ANY = r"(?:hey|hej|hi|hello|hallo|ok|okay|salam|salaam|سلام|هی)"
_WAKE_ANY = re.compile(rf"(?:^|\s)(?P<greet>{GREET_ANY})\s+(?P<name>{NAME})\b(?P<rest>.*)$")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "").lower().translate(_FA_MAP)
    t = t.replace("’", "'").replace("-", " ")
    t = re.sub(r"[^\w\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# A bare name (no greeting) only wakes when it stands alone: "Vector, what
# time is it?", "Vector!", "Vector". "Victor Hugo wrote..." (live TV test,
# 10 Oct) runs straight into the next word and is not a wake.
_BARE = re.compile(rf"^\W*(?:(?:oh|so|ok|okay|well|um|uh|خب)\W+)?{NAME}\s*(?:[,!.?،:;؛]|$)")


def match_wake(text: str, bare: bool = True) -> tuple[bool, str]:
    """(hit, rest). Hits: the name opening the sentence after an optional
    greeting ("Vector, ...", "Hey Victor ...", "Hallo Vektor", "سلام وکتور"),
    or greeting + name anywhere ("so um, hey Vector, what time is it"), or a
    greeting first and then a near-miss of the name ("Hey Vecta", "Evektor",
    "Hey Becca" alone). bare=False requires the greeting. rest = what
    followed (normalised)."""
    t = norm(text)
    m = _WAKE_START.match(t)
    if m and (m.group("greet") or (bare and _BARE.match(unicodedata.normalize("NFC", text or "").lower()))):
        pass
    else:
        m = _WAKE_ANY.search(t)
        if not m:
            return _fuzzy_wake(text, t, bare)
    rest = m.group("rest").strip()
    rest = re.sub(r"^(?:please|bitte|لطفا)\s*$", "", rest).strip()
    return True, rest


# --- near misses (live 10 Oct: a quick "Hey Vector" from across the room) ---
# Only at the very start, right after a greeting (or the whole utterance being
# the name), so a sentence that merely contains a Victor-like word never wakes.
_FUZZY_GREET = {"hey", "hay", "hei", "hej", "heh", "hi", "hello", "hallo", "halo", "hallå", "ok", "okay",
                "salam", "salaam", "سلام", "هی", "های", "эй", "хей", "хэй", "привет"}
_FILLERS = {"oh", "so", "um", "uh", "well", "and", "ja", "na", "خب"}
_FORMS = ("vector", "victor", "vektor", "viktor", "wektor", "вектор", "виктор", "وکتور", "ویکتور", "وکتر")
# Real words / names one or two edits from the name that must not wake.
_NOT_NAME = {"victoria", "viktoria", "victory", "vectors", "vektoren", "victors", "factor", "faktor", "sector",
             "sektor", "hector", "rector", "rektor", "doctor", "doktor", "actor", "vecto", "viktorija",
             "wecker", "vetter", "better", "letter", "dekor", "decor", "victim", "victims", "vista", "viktim"}
# "Victor Hugo"-style: a first word that makes "<word> <name>" a statement, not a call
_NOT_FIRST = {"the", "a", "an", "my", "your", "his", "her", "our", "their", "this", "that", "der", "die", "das",
              "ein", "eine", "mein", "dein", "sein", "is", "was", "and", "or", "not", "kein", "von", "of", "by", "from",
              "with", "mit", "to", "for", "für", "in", "on", "at", "about", "über", "و", "این", "آن", "با", "از", "به"}
_FIRST = set("vwbfeiy") | {"в", "و", "ب", "ف"}


def _dist(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance (stops counting at cap)."""
    if abs(len(a) - len(b)) >= cap:
        return cap
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) >= cap:
            return cap
        prev = cur
    return min(prev[-1], cap)


def _skeleton(w: str) -> str:
    """Rough consonant skeleton: Vector/Victor/Wektor/Becca/Vecta -> vk(t)(r)."""
    w = w.lower().replace("ck", "k").replace("ph", "f")
    out = []
    for ch in w:
        c = {"v": "v", "w": "v", "b": "v", "f": "v", "c": "k", "k": "k", "q": "k", "x": "k",
             "t": "t", "d": "t", "r": "r"}.get(ch, "" if ch in "aeiouyäöüé'" else "?")
        if c and (not out or out[-1] != c):
            out.append(c)
    return "".join(out)


def name_like(word: str, strict: bool = False) -> bool:
    """A transcript word that is plausibly the name. strict: edit distance only."""
    w = word.lower().strip("'")
    if len(w) < 4 or w in _NOT_NAME or w[0] not in _FIRST:
        return False
    glued = w[0] in "eiy"
    if glued:  # "Evektor", "Ivector": a swallowed greeting; must then be close
        if len(w) < 6:
            return False
        w = w[1:]
    if w in _NOT_NAME:
        return False
    if min(_dist(w, f) for f in _FORMS) <= (1 if glued or len(w) < 5 else 2):
        return True
    if glued:
        return False
    return not strict and w[0] in "vwbf" and len(w) <= 7 and _skeleton(w) in ("vk", "vkt", "vktr", "vkr")


def _fuzzy_wake(raw: str, t: str, bare: bool) -> tuple[bool, str]:
    words = t.split()
    i = 0
    while i < len(words) and words[i] in _FILLERS:
        i += 1
    if i >= len(words):
        return False, ""
    w = words[i:]
    greet = w[0] in _FUZZY_GREET
    if greet and len(w) >= 2:
        # "Hey Vecta, ..." / "Hey Vic tor": next word, or the next two joined
        cand = [(w[1], 2)] + ([(w[1] + w[2], 3)] if len(w) >= 3 else [])
        for word, used in cand:
            if not name_like(word, strict=True):
                continue
            return True, " ".join(w[used:])
        # loose skeleton only when the greeting + name is the whole utterance ("Hey Becca.")
        if len(w) == 2 and name_like(w[1]):
            return True, ""
        return False, ""
    # "Evektor" / "Heyvector": greeting glued on, opening the utterance
    m = re.match(r"^(?:hey|hei|hej|hay|hi|he|e|a|i)(\w{4,})$", w[0])
    if m and _dist(m.group(1), "vector") <= 1 or (m and min(_dist(m.group(1), f) for f in _FORMS) <= 1):
        return True, " ".join(w[1:])
    # the name alone as the whole utterance ("Vecta.", "Wektor!")
    if bare and len(w) == 1 and name_like(w[0], strict=True):
        return True, ""
    # two words ending on the name: "E-Vektor.", "Eh Vector!", "شون، وکتور" (a cut or
    # misheard greeting). Only the exact name here, and nothing may follow it.
    if bare and len(w) == 2 and re.fullmatch(NAME, w[1]) and len(w[0]) <= 5 and w[0] not in _NOT_FIRST:
        return True, ""
    return False, ""


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


# --- garbled short transcripts while awake ("Mof Berlin." live 10 Oct) ---
_KNOWN = set("""the a an is are was were be i you he she it we they what when where why how who which this that
to of in on at for with and or but not do does did can could would will my your me hello hi hey please thanks
thank yes no yeah yep nope time weather today tell about there here ok okay cool nice great good wow awesome
super sorry bye goodbye night morning louder quieter stop go come look sing dance joke jokes again more less
right left up down fine true really what's it's i'm you're don't can't play music photo picture picture
der die das ein eine ist sind ich du er sie es wir nicht und oder aber wie was wann wo warum wer mit von zu
im auf für heute bitte danke ja nein hallo guten tag wetter uhr spät mir mich dir dich kannst kann sag
gut toll schön lauter leiser tschüss nacht morgen genau stimmt echt nochmal witz""".split())


def unclear(text: str) -> bool:
    """A 1-2 word Latin transcript with no everyday word in it: probably STT
    guessing at noise or a cut-off phrase. Persian is never flagged."""
    t = norm(text)
    if not t or re.search(r"[\u0600-\u06ff]", t):
        return False
    words = re.findall(r"[a-zäöüß']+", t)
    if not words or len(words) > 2:
        return False
    return not any(w in _KNOWN for w in words)
