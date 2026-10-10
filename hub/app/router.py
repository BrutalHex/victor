"""Command-or-chat router: one small OpenAI call (text only) per transcript.

Returns strict JSON (structured outputs) {"type", "intent", "args", "confidence"}.
The fixed patterns in intents.py are only a fast path for short, exact commands
("turn left", "stopp", "fist bump"); everything else is decided here. Below
ROUTER_MIN_CONF, or on any error, the turn goes to chat (or, if the API is down,
to the pattern match it would have had before).
"""

from __future__ import annotations

import json
import re
import os
import time

import intents as I

MODEL = os.environ.get("HUB_ROUTER_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
MIN_CONF = float(os.environ.get("HUB_ROUTER_MIN_CONF", "0.6"))
TIMEOUT_S = float(os.environ.get("HUB_ROUTER_TIMEOUT_S", "4"))
FAST_MAX_WORDS = int(os.environ.get("HUB_ROUTER_FAST_WORDS", "4"))
ON = os.environ.get("HUB_ROUTER", "1").strip().lower() not in ("0", "false", "no", "off")

IDS = [n for n, *_ in I._DEF]

# Short meaning per intent for the prompt (status/notes in intents.py say what works).
DESC = {
    "intent_system_charger": "go back to the charger / go home / dock",
    "intent_system_leavecharger": "get/drive off the charger",
    "intent_imperative_come": "come here / come to me",
    "intent_imperative_forward": "drive/move forward a bit",
    "intent_imperative_backup": "back up / reverse a bit",
    "intent_imperative_turnleft": "turn left",
    "intent_imperative_turnright": "turn right",
    "intent_imperative_turnaround": "turn around",
    "intent_imperative_lookatme": "look at me",
    "intent_play_fistbump": "give a fist bump",
    "intent_photo_take_extend": "take a photo/picture/selfie",
    "intent_clock_checktimer": "how much time is left on the timer",
    "intent_global_stop_extend": "stop/cancel the timer or alarm",
    "intent_clock_settimer_extend": "set a timer (args.duration = the spoken duration, e.g. '5 minutes')",
    "intent_clock_time": "what time is it",
    "intent_character_age": "how old are you",
    "intent_imperative_dance": "dance (now)",
    "intent_play_anytrick": "do a trick",
    "intent_imperative_praise": "praise the robot: good robot / well done",
    "intent_imperative_abuse": "scold/insult the robot: bad robot",
    "intent_imperative_apologize": "the user apologises to the robot (I'm sorry)",
    "intent_imperative_love": "the user says I love you / you're cute to the robot",
    "intent_imperative_volumeup": "louder / volume up",
    "intent_imperative_volumedown": "quieter / volume down",
    "intent_imperative_volumelevel_extend": "set volume to a level (args.level = '1'..'5', 'min', 'max', 'medium')",
    "intent_imperative_shutup": "be quiet / shut up / stop talking or moving",
    "intent_system_sleep": "go to sleep",
    "intent_system_wake": "wake up",
    "intent_greeting_hello": "a bare greeting (hi / hello / hallo / سلام)",
    "intent_greeting_goodmorning": "good morning",
    "intent_greeting_goodnight": "good night",
    "intent_greeting_goodbye": "goodbye / see you",
    "intent_seasonal_happynewyear": "happy new year",
    "intent_seasonal_happyholidays": "happy holidays / merry christmas",
    "intent_names_username_extend": "the user tells their own name: my name is X / I am X (args.name = X)",
    "intent_play_rollcube": "roll the cube",
    "intent_imperative_findcube": "find the cube",
    "intent_imperative_fetchcube": "bring/fetch the cube",
    "intent_play_pickupcube": "pick up the cube",
    "intent_play_keepaway": "play keepaway",
    "intent_play_popawheelie": "pop a wheelie",
    "intent_play_blackjack": "play blackjack",
    "intent_explore_stop": "stop exploring / stop wandering around",
    "intent_explore_start": "go explore / wander / drive around on your own",
    "intent_imperative_eyecolor": "change your eye colour",
    "intent_message_recordmessage_extend": "record a message for someone",
    "intent_message_playmessage_extend": "play my messages",
    "intent_amazon_signin": "sign in/out of Alexa",
}

SCHEMA = {
    "name": "route",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "intent", "args", "confidence"],
        "properties": {
            "type": {"type": "string", "enum": ["command", "chat"]},
            "intent": {"type": ["string", "null"], "enum": IDS + [None]},
            "args": {
                "type": "object",
                "additionalProperties": False,
                "required": ["duration", "level", "name"],
                "properties": {
                    "duration": {"type": ["string", "null"]},
                    "level": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                },
            },
            "confidence": {"type": "number"},
        },
    },
}

PROMPT = (
    "You route what a person said to Vector, a small home robot, transcribed from speech. The text may be "
    "English, German or Persian (Farsi), sometimes with a wake word like 'Hey Vector' / 'سلام وکتور'.\n"
    "Decide: is it a direct request to the robot to perform one of the commands below RIGHT NOW (type=command, "
    "intent=that id), or anything else (type=chat, intent=null): conversation, opinions, questions about a "
    "topic, stories, past or hypothetical actions, talking ABOUT a command, questions about who someone is.\n"
    "Examples: 'what do you think about dancing' -> chat. 'can you dance for me' -> intent_imperative_dance. "
    "'I turned left yesterday' -> chat. 'turn left' -> intent_imperative_turnleft. 'do you like taking photos' -> chat. "
    "'Wie spät ist es?' -> intent_clock_time. 'Ich habe gestern getanzt' -> chat. 'برقص' -> intent_imperative_dance. "
    "'رقص دوست داری؟' -> chat. 'who am I' / 'do you know me' -> chat. Polite forms (please, could you, kannst du, "
    "میشه) and filler words (by the way, eigentlich, mal, الان) do not change the decision. Questions a command answers "
    "(what time is it / Wie spät ist es eigentlich? / ساعت چنده, how old are you, how long is left on my timer) are commands.\n"
    "Fill args only for the commands that name them, else null. confidence = how sure you are (0..1).\n"
    "Commands:\n" + "\n".join(f"- {k}: {v}" for k, v in DESC.items())
)


def fast(text: str) -> I.Intent | None:
    """Exact pattern match on a short utterance: no API call needed."""
    m = I.match(text)
    if m is None:
        return None
    t = I.normalise(text)
    if len(t.split()) > FAST_MAX_WORDS or text.strip().endswith("?") and m.name != "intent_clock_time":
        return None
    return m


def to_intent(d: dict, text: str, lang: str) -> I.Intent | None:
    """Router JSON -> Intent, or None for chat / unsure / unknown."""
    if not isinstance(d, dict) or d.get("type") != "command":
        return None
    name = d.get("intent")
    if name not in IDS:
        return None
    try:
        conf = float(d.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if conf < MIN_CONF:
        return None
    a = {k: re.sub(r"[{}\[\]\"]+", " ", str(v)).strip(" ,.") if v else v for k, v in (d.get("args") or {}).items()}
    arg = ""
    if name == "intent_clock_settimer_extend":
        arg = str(a.get("duration") or "")
    elif name == "intent_imperative_volumelevel_extend":
        arg = str(a.get("level") or "")
    elif name == "intent_names_username_extend":
        arg = str(a.get("name") or "").strip()
        if not arg:
            return None
    lang = lang if lang in ("en", "de", "fa") else "en"
    return I.Intent(name=name, lang=lang, arg=arg, text=I.normalise(text))


def classify(api, text: str) -> tuple[dict | None, int]:
    """One structured-output call. Returns (json or None, ms)."""
    t0 = time.monotonic()
    body = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 80,
        "response_format": {"type": "json_schema", "json_schema": SCHEMA},
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}],
    }
    try:
        raw = api.post("/v1/chat/completions", json.dumps(body).encode(), "application/json", TIMEOUT_S)
        msg = json.loads(raw.decode())["choices"][0]["message"]
        d = json.loads(msg.get("content") or "null")
    except Exception as exc:  # noqa: BLE001 - any failure means 'use the patterns'
        print(f"router error {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        d = None
    return d, int((time.monotonic() - t0) * 1000)


def route(api, text: str, lang: str, has_key: bool = True) -> tuple[I.Intent | None, str]:
    """-> (intent or None for chat, how) and logs the decision."""
    if not text:
        return None, "empty"
    f = fast(text)
    if f is not None:
        print(f"router fast intent={f.name} text={text!r}", flush=True)
        return f, "fast"
    if not ON or not has_key:
        m = I.match(text)
        return m, "patterns"
    d, ms = classify(api, text)
    if d is None:
        m = I.match(text)  # API down: behave like before
        print(f"router fallback patterns intent={m.name if m else None} ms={ms}", flush=True)
        return m, "fallback"
    it = to_intent(d, text, lang)
    print(f"router llm type={d.get('type')} intent={d.get('intent')} args={json.dumps(d.get('args'), ensure_ascii=False)} "
          f"conf={d.get('confidence')} -> {it.name if it else 'chat'} ms={ms} text={text!r}", flush=True)
    return it, "llm"
