"""Command priority: ONE ordered table, first match wins (M.O, 12 Oct 2026:
"reorder the whole list of commands, they are getting mixed").

Every voice turn goes through `route(text, asleep)`:

  #  rule         asleep?  what
  1  sleep        yes      'Stop Vector' / 'Vector stop' / 'Hey Vector, stop' / 'stop listening' / 'go to sleep'
                           (EN/DE/FA). Cancels plan, explore, dance, sound; asleep eyes. Asleep already: no-op.
  2  halt         no       plain 'stop' / 'halt' / 'stopp' / 'ایست' / 'shut up': cancel motion/dance/explore, stay awake
  3  wake         yes      'Hey Vector' (asleep: wake + 'Yes?'; awake: chime + 'Yes?'). After a VOICE sleep
                           (rule 1) voice wake is off: only the back button wakes him (M.O 14 Oct). 'Hey Vector, <cmd>':
                           wake, then <cmd> goes through the table again (stop -> rule 1)
  4  asleep_gate  -        asleep: nothing below runs (button, asleep music head bob and the silent
                           face look-up live outside the voice path and never open the session)
  5  ssh          no       SSH on/off/status (exact phrases)
  6  command      no       exact short robot commands (fast path: time, timer, volume, moves, tricks...)
  7  pet          no       'who is my dog/cat' pet answers, animal sounds
  8  music        no       dance to the music, hum
  9  face         no       'my name is ...', 'who am I'
 10  brain        no       everything else: OpenAI (reply + expression + plan), with its guards

Rules 5-9 come from intents.match(); its patterns are tried in this
category order, so a pet pattern can never shadow an exact command.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import intents
import router
import wake

RULES = [  # (name, allowed while asleep, languages, example phrases)
    ("sleep", True, "en de fa", ["Stop Vector", "Vector stop", "Hey Vector, stop", "stop listening", "go to sleep",
                                 "Stopp Vektor", "geh schlafen", "hör auf zuzuhören", "وکتور بسه", "بخواب"]),
    ("halt", False, "en de fa", ["stop", "halt", "shut up", "be quiet", "stopp", "sei still", "ایست", "بسه", "وایسا"]),
    ("wake", True, "en de fa", ["Hey Vector", "Vector!", "Hallo Vektor", "سلام وکتور", "Hey Vector, what time is it?"]),
    ("asleep_gate", True, "-", ["(anything else while asleep is ignored)"]),
    ("ssh", False, "en de fa", ["turn on SSH", "SSH aus", "is SSH on?"]),
    ("command", False, "en de fa", ["what time is it", "set a timer for 5 minutes", "louder", "turn left", "dance"]),
    ("pet", False, "en de fa", ["who is my dog?", "who's my kitty?", "bark like a dog", "سگم کیه؟", "Wer ist meine Katze?"]),
    ("music", False, "en de fa", ["dance to the music", "hum this", "tanz zur Musik"]),
    ("face", False, "en de fa", ["my name is Ann", "who am I", "wer bin ich"]),
    ("brain", False, "en de fa", ["(everything else)"]),
]
ORDER = [r[0] for r in RULES]
ASLEEP_OK = {r[0] for r in RULES if r[1]}

HALT_INTENTS = {"intent_imperative_shutup"}
SLEEP_INTENTS = {"intent_system_sleep"}

# 'go to sleep' & co. (with or without the name) end the session too
_SLEEP = re.compile(
    r"^(?:(?:ok|okay|now|jetzt|please|bitte|خب|لطفا)\s+)*(?:"
    r"(?:go to sleep|go to bed|sleep now|time to sleep|time for bed|go sleep|sleep)"
    r"|(?:geh |gehe )?schlafen(?: gehen)?|schlaf(?:e)?(?: jetzt)?"
    r"|(?:برو )?بخواب|وقت خوابه"
    r")(?:\s+(?:now|jetzt|please|bitte|لطفا|دیگه))*$")


def category(intent_name: str) -> str:
    """intents.py name -> rule 5-9."""
    n = intent_name or ""
    if n.startswith("intent_system_ssh"):
        return "ssh"
    if n in ("intent_pet_whoismy", "intent_animal_sound"):
        return "pet"
    if n.startswith("intent_music_"):
        return "music"
    if n.startswith("intent_names_"):
        return "face"
    return "command"


def _sleepish(t: str) -> bool:
    """t without the wake phrase: a session stop, a plain halt or 'go to sleep'."""
    if not t:
        return False
    if wake.is_session_stop(t) or _SLEEP.match(wake.norm(t)):
        return True
    m = intents.match(t)
    return bool(m and (m.name in HALT_INTENTS or m.name in SLEEP_INTENTS))


def is_sleep(text: str) -> bool:
    """Rule 1. 'Stop Vector', 'Hey Vector, stop', 'go to sleep', 'Vector, geh schlafen'."""
    if not text:
        return False
    if wake.is_session_stop(text):
        return True
    t = wake.norm(text)
    if _SLEEP.match(t):
        return True
    hit, rest = wake.match_wake(text)
    if hit and rest:
        return _sleepish(rest)
    m = re.match(rf"^(?P<rest>.+?)\s+{wake.NAME}$", t)  # 'stop it Vector', 'go to sleep Vector'
    if m:
        return _sleepish(m.group("rest"))
    return False


_WHOAMI = re.compile(r"\b(?:who am i|do you know me|do you know who i am|wer bin ich|kennst du mich|من کی ام|من کیم|منو میشناسی)\b")


@dataclass
class Route:
    rule: str  # one of ORDER
    text: str  # what the next stage should see (wake phrase stripped)
    intent: intents.Intent | None = None


def route(text: str, asleep: bool) -> Route:
    """First matching rule for one transcript. Rules 1-4 decide here; for
    awake turns rules 5-9 use the (category-ordered) fast path, else brain."""
    text = (text or "").strip()
    if not text:
        return Route("asleep_gate" if asleep else "brain", "")
    if is_sleep(text):
        return Route("sleep", text)
    hit, rest = wake.match_wake(text)
    if hit:
        return Route("wake", wake.strip_wake(text) if rest else "")
    if asleep:
        return Route("asleep_gate", text)
    return classify_awake(text)


def classify_awake(text: str) -> Route:
    m = router.fast(text)
    if m is None:
        f = intents.match(text)  # name/face intents are not on the fast path; main.py runs them on its face path
        if f is not None and category(f.name) == "face":
            return Route("face", text, f)
        if _WHOAMI.search(wake.norm(text)):
            return Route("face", text)
        return Route("brain", text)
    if m.name in HALT_INTENTS:
        return Route("halt", text, m)
    return Route(category(m.name), text, m)
