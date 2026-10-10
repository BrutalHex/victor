"""Spoken-language policy: only the languages in HUB_LANGS (default en,de,fa).

gpt-4o-mini-transcribe only returns text (no detected language), so the hub
classifies the transcript itself: script first (Latin / Arabic / CJK /
Cyrillic ...), then English vs German by function words and umlauts, and
Persian vs Arabic by letters. Anything else is a mis-hearing or noise.
"""
from __future__ import annotations

import os
import re
import unicodedata

NAMES = {"en": "English", "de": "German (Deutsch)", "fa": "Persian (Farsi)"}
DEFAULT = "en,de,fa"


def allowed(env: str | None = None) -> list[str]:
    raw = os.environ.get("HUB_LANGS", DEFAULT) if env is None else env
    out = [x.strip().lower() for x in raw.split(",") if x.strip().lower() in NAMES]
    return out or ["en"]


def stt_prompt(langs: list[str], wake: bool = False) -> str:
    names = ", ".join(NAMES[x] for x in langs)
    out = (f"A person talks to a small robot named Vector (the name is spelled Vector). "
           f"The speech is in one of: {names}.")
    if wake:  # asleep: the only thing that matters is the wake phrase
        out += " To wake the robot they say Hey Vector (German: Hallo Vektor, Persian: سلام وکتور)."
    return out


def reply_rule(langs: list[str]) -> str:
    names = ", ".join(NAMES[x].split(" (")[0] for x in langs)
    first = NAMES[langs[0]].split(" (")[0]
    rule = (
        f"Always reply in the same language the user spoke, which is one of: {names}. "
        f"Never reply in any other language; if the language is unclear, reply in {first}."
    )
    if "fa" in langs:
        rule += " For Persian, write in Persian (Perso-Arabic) script, not transliteration."
    return rule


# Latin letters that are normal in English or German text (incl. loanwords like café).
_LATIN_OK = set("abcdefghijklmnopqrstuvwxyzäöüßéèêëàáâîïôçñ")
_TURKISH = set("ğşıİŞĞ")
_PERSIAN_ONLY = set("پچژگکیی")  # پ چ ژ گ and Persian kaf/yeh
_ARABIC_ONLY = set("ةيكىإأؤ")
_ZW = {"\u200c", "\u200d", "\u200e", "\u200f"}

_EN = set("""the a an is are was were be i you he she it we they what when where why how who which
this that these those to of in on at for with and or but not do does did can could would will
my your me hello hi please thanks thank yes no time weather today tell about there here""".split())
_DE = set("""der die das ein eine einen ist sind war bin bist ich du er sie es wir ihr nicht und oder
aber wie was wann wo warum wer welche welcher mit von zu im auf für ist's heute bitte danke ja nein
hallo guten tag wetter uhr wieviel spät mir mich dir dich kannst kann sag erzähl über noch""".split())
_OTHER = set("""el los las una por para que como está muy les des est pas avec pour mais je vous il
het een niet ook van zijn che non sono della ser bir bu ve için çok değil ne nasıl""".split())


def script_counts(text: str) -> dict[str, int]:
    c = {"latin": 0, "arabic": 0, "cyrillic": 0, "cjk": 0, "other": 0}
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("LATIN"):
            c["latin"] += 1
        elif name.startswith("ARABIC"):
            c["arabic"] += 1
        elif name.startswith("CYRILLIC"):
            c["cyrillic"] += 1
        elif name.startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL")):
            c["cjk"] += 1
        else:
            c["other"] += 1
    return c


def classify(text: str, langs: list[str] | None = None) -> tuple[str, str]:
    """Return (lang, reason). lang is "" when the text is not in an allowed
    language; reason names what it looked like (zh, ru, tr, ar, latin-other...)."""
    langs = langs or allowed()
    t = text.strip()
    c = script_counts(t)
    letters = sum(c.values())
    if letters == 0:
        return "", "no-letters"
    top = max(c, key=c.get)
    if c[top] < 0.85 * letters:
        return "", "mixed-script"
    if top == "cjk":
        return "", "cjk"
    if top == "cyrillic":
        return "", "cyrillic"
    if top == "other":
        return "", "other-script"
    if top == "arabic":
        chars = set(t)
        if chars & _PERSIAN_ONLY:
            return ("fa", "persian") if "fa" in langs else ("", "fa")
        if chars & _ARABIC_ONLY:
            return "", "arabic"
        return ("fa", "arabic-script") if "fa" in langs else ("", "fa")
    # Latin
    if set(t) & _TURKISH:
        return "", "turkish"
    low = t.lower()
    odd = sum(1 for ch in low if ch.isalpha() and ch not in _LATIN_OK)
    if odd > max(1, 0.03 * letters):
        return "", "latin-other"
    words = re.findall(r"[a-zäöüßéèêëàáâîïôçñ']+", low)
    en = sum(w in _EN for w in words)
    de = sum(w in _DE for w in words) + 2 * sum(ch in "äöüß" for ch in low)
    other = sum(w in _OTHER for w in words)
    if other > en + de:
        return "", "latin-other"
    if en == 0 and de == 0 and len(words) >= 4:
        return "", "latin-unknown"
    lang = "de" if de > en else "en"
    if lang not in langs:
        alt = "en" if lang == "de" else "de"
        if alt in langs and (en if alt == "en" else de) > 0:
            return alt, "latin"
        return "", lang
    return lang, "latin"


def retry_language(reason: str, langs: list[str]) -> str:
    """Most likely allowed language to force on the one STT retry."""
    if reason in ("arabic", "fa", "arabic-script") and "fa" in langs:
        return "fa"
    if reason in ("turkish", "latin-other", "latin-unknown") and "de" in langs and "en" not in langs:
        return "de"
    return langs[0]
