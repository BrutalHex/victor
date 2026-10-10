"""One OpenAI call per voice turn: the spoken reply, the face expression that
fits it, and (optionally) a short motion plan, all in one Responses API call
with structured output (text.format json_schema) and the web_search tool.

It replaces the old two calls (router classify + web chat) for everything that
is not on the local fast path. The fast path (intents.py patterns, short exact
commands, "stop"/"halt"/"stopp"/"ایست"), SSH and "Stop Vector" (sleep) never
reach the model.

The model only *names* steps. Every step is validated and clamped here and
again on the robot (robot/agent/internal/action/plan.go), whose veto (cliff,
pickup, fall, battery, heartbeat, obstacle) owns the motors and can abort.
"""

from __future__ import annotations

import json
import math
import os
import re
import time

import intents as I

ON = os.environ.get("HUB_BRAIN", "1").strip().lower() not in ("0", "false", "no", "off")
MAX_TOKENS = int(os.environ.get("HUB_BRAIN_MAX_TOKENS", "500"))
GESTURES = os.environ.get("HUB_EXPRESSION_GESTURES", "1").strip().lower() not in ("0", "false", "no", "off")

# ------------------------------------------------------------ expressions
# name -> (face clip on the robot, small head gesture as plan steps, meaning).
# Clips: robot/agent/internal/face/ddl/face_clips.json.gz (DDL eye poses /
# stock animations, tools/ddl_faceclips.py). Every clip ends on normal eyes.
EXPRESSIONS: dict[str, tuple[str, str, str]] = {
    "neutral": ("", "", "calm, matter-of-fact answers, facts, numbers"),
    "happy": ("expr_happy", "head bob", "glad, friendly, good news, thanks"),
    "joy": ("expr_joy", "head bob", "warm delight, smiling"),
    "excited": ("expr_excited", "head nod", "thrilled, can't wait, eureka, great idea"),
    "love": ("expr_love", "head up", "affection, compliments to you, 'I love you'"),
    "proud": ("expr_proud", "head up", "praised, did it, accomplished"),
    "sad": ("expr_sad", "head droop", "sad news, sympathy, missing someone"),
    "hurt": ("expr_hurt", "head droop", "insulted, scolded, feelings hurt"),
    "angry": ("expr_angry", "", "annoyed, indignant, playful grumpiness"),
    "frustrated": ("expr_frustrated", "", "it didn't work, stuck, can't do it"),
    "surprised": ("expr_surprised", "head up", "wow, unexpected, amazing fact"),
    "awe": ("expr_awe", "head up", "wonder, space, something huge or beautiful"),
    "confused": ("expr_confused", "", "don't understand, strange question"),
    "curious": ("expr_curious", "", "interested, asking back, exploring an idea"),
    "thinking": ("expr_thinking", "", "pondering, a tricky question, unsure answer"),
    "scared": ("expr_scared", "", "frightened, spooky, danger, edges"),
    "worried": ("expr_worried", "", "concerned for you, warnings, bad weather"),
    "sleepy": ("expr_sleepy", "head droop", "tired, bedtime, late at night"),
    "bored": ("expr_bored", "", "boring topic, nothing to do"),
    "shy": ("expr_shy", "head down", "embarrassed, flattered, blushing"),
    "disgusted": ("expr_disgusted", "", "yuck, gross food or smells"),
    "suspicious": ("expr_suspicious", "", "skeptical, 'are you sure?', jokes on you"),
    "determined": ("expr_determined", "", "let's do this, ready, focused on a task"),
}
NAMES = list(EXPRESSIONS)
ALIASES = {
    "tired": "sleepy", "embarrassed": "shy", "affection": "love", "loving": "love", "glad": "happy",
    "mad": "angry", "furious": "angry", "afraid": "scared", "fear": "scared", "wow": "surprised",
    "shocked": "surprised", "unsure": "confused", "bored": "bored", "gross": "disgusted", "normal": "neutral",
    "calm": "neutral", "pondering": "thinking", "interested": "curious", "fröhlich": "happy", "traurig": "sad",
    "wütend": "angry", "müde": "sleepy",
}


def expression(name) -> str:
    """Model/user text -> canonical expression name ('' = none/neutral)."""
    n = str(name or "").strip().lower()
    n = ALIASES.get(n, n)
    return n if n in EXPRESSIONS and n != "neutral" else ""


def clip(name) -> str:
    n = expression(name)
    return EXPRESSIONS[n][0] if n else ""


# ------------------------------------------------------------ commands
MAX_STEPS = 12
MAX_DRIVE_MM = 500.0
MAX_TURN_DEG = 720.0
MAX_WAIT_MS = 5000
MAX_TRAVEL_MM = 2000.0
MAX_PLAN_S = 30.0
HALF_TRACK_MM = 24.0  # drivectl.HalfTrackMM
HEAD = ("up", "down", "middle", "nod")
LIFT = ("up", "down")
# named robot tricks (action.Tricks in the agent); wheels marked
TRICKS = {
    "dance": True, "nod": False, "fistbump": False, "look_at_me": False, "look_up": False, "look_down": False,
    "come_here": True, "forward": True, "backup": True, "turn_left": True, "turn_right": True, "turn_around": True,
}
TRICK_TRAVEL = {"dance": 60 * math.pi / 180 * HALF_TRACK_MM, "come_here": 100, "forward": 60, "backup": 120,
                "turn_left": 90 * math.pi / 180 * HALF_TRACK_MM, "turn_right": 90 * math.pi / 180 * HALF_TRACK_MM,
                "turn_around": math.pi * HALF_TRACK_MM}
TRICK_S = {"dance": 7.5, "nod": 0.9, "fistbump": 7.0, "look_at_me": 2.2, "look_up": 0.6, "look_down": 0.6,
           "come_here": 2.0, "forward": 1.4, "backup": 1.9, "turn_left": 1.4, "turn_right": 1.4, "turn_around": 2.0}
DO = ["drive", "turn", "head", "lift", "wait", "expression", "trick", "explore", "explore_stop", "stop", "volume"]

# stock commands the model may pick as "intent" (single, hub-handled). Never
# SSH (local only) and never session sleep ("Stop Vector" is local).
# Only commands that need hub state the model doesn't have (timers, camera,
# face DB, charger docking, the robot's birthday). Everything else is either a
# move (actions) or something the model answers itself (time: the clock is in
# the prompt). A small list keeps it from picking e.g. clock_time for weather.
INTENT_IDS = [n for n in ("intent_clock_settimer_extend", "intent_clock_checktimer", "intent_global_stop_extend",
                          "intent_photo_take_extend", "intent_names_username_extend", "intent_system_charger",
                          "intent_system_leavecharger", "intent_system_sleep", "intent_character_age",
                          "intent_play_fistbump") if n in {d[0] for d in I._DEF}]


def _speeds() -> tuple[float, float]:
    def f(k, d, lo, hi):
        try:
            return max(lo, min(hi, float(os.environ.get(k, d))))
        except ValueError:
            return float(d)
    drive = f("VECTOR_DRIVE_MMPS", 120, 30, 140)
    turn = f("VECTOR_TURN_DPS", 180, 30, 300)
    return drive, turn * math.pi / 180 * HALF_TRACK_MM


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


_UNIT = re.compile(r"(?<=\d)\s*(?:mm|ms|deg|degrees|°)\b|(?<=\d)°")
_STEP = re.compile(r"^\s*([a-z_]+)\s*[:=]?\s*(.*?)\s*$")


def step_dict(a) -> dict | None:
    """'drive 200' / 'turn -90' / 'head up' / 'trick dance' / 'volume 3' (the
    model's compact form) or an already-split dict -> {"do", "mm", ...}."""
    if isinstance(a, dict):
        return a
    if not isinstance(a, str):
        return None
    m = _STEP.match(_UNIT.sub("", a.strip().lower()))
    if not m:
        return None
    do, arg = m.group(1), m.group(2).strip().split(" ")[0] if m.group(2).strip() else ""
    d = {"do": do, "mm": None, "deg": None, "ms": None, "pos": None, "name": None, "level": None}
    if do == "drive":
        d["mm"] = arg
    elif do == "turn":
        d["deg"] = arg
    elif do == "wait":
        d["ms"] = arg
    elif do in ("head", "lift"):
        d["pos"] = arg
    elif do in ("expression", "face", "trick"):
        d["do"] = "expression" if do == "face" else do
        d["name"] = arg
    elif do == "volume":
        d["level"] = arg
    return d


def expand(actions) -> list:
    """'circle [diameter mm] [left|right]' -> a hexagon: 6 x (drive side, turn
    60) = 12 movement steps (the model kept stopping at 4 legs)."""
    out: list = []
    for a in list(actions or [])[: MAX_STEPS * 3]:
        if isinstance(a, str) and a.strip().lower().startswith("circle"):
            f = _UNIT.sub("", a.strip().lower()).split()[1:]
            dia = next((_num(x) for x in f if _num(x) is not None), None) or 300.0
            side = max(40.0, min(250.0, abs(dia) / 2))
            turn = -60 if "right" in f else 60
            out += [f"drive {side:.0f}", f"turn {turn}"] * 6
        else:
            out.append(a)
    return out


def build_plan(actions, start_expr: str = "", end_expr: str = "", on_charger: bool | None = False) -> dict:
    """Validate + clamp the model's actions into the agent's plan text.

    -> {"steps": [...wire steps], "plan": "plan:..." or "", "hub": [hub-side
    actions: ("volume", n) / ("explore", None) / ("explore_stop", None) /
    ("stop", None)], "notes": [...], "est_s": seconds, "travel_mm": mm,
    "wheels": bool, "held": bool (wheel steps dropped on the charger)}"""
    drive_mmps, turn_mmps = _speeds()
    steps: list[str] = []
    hub: list[tuple[str, object]] = []
    notes: list[str] = []
    est = travel = 0.0
    wheels = held = False
    on = on_charger is not False  # unknown counts as on the charger
    if start_expr and clip(start_expr):
        steps.append(f"face {clip(start_expr)}")
    for a in expand(actions)[: MAX_STEPS * 3]:
        a = step_dict(a)
        if a is None:
            continue
        do = str(a.get("do") or "").strip().lower()
        step, dt, dist, wheel = "", 0.0, 0.0, False
        if do == "drive":
            mm = _num(a.get("mm"))
            if mm is None or abs(mm) < 5:
                notes.append(f"drive {a.get('mm')!r} dropped")
                continue
            mm = max(-MAX_DRIVE_MM, min(MAX_DRIVE_MM, mm))
            step, dt, dist, wheel = f"drive {mm:.0f}", abs(mm) / drive_mmps + 0.8, abs(mm), True
        elif do == "turn":
            deg = _num(a.get("deg"))
            if deg is None or abs(deg) < 3:
                notes.append(f"turn {a.get('deg')!r} dropped")
                continue
            deg = max(-MAX_TURN_DEG, min(MAX_TURN_DEG, deg))
            arc = abs(deg) * math.pi / 180 * HALF_TRACK_MM
            step, dt, dist, wheel = f"turn {deg:.0f}", arc / turn_mmps + 0.8, arc, True
        elif do == "head":
            pos = str(a.get("pos") or "").lower()
            pos = {"center": "middle", "mid": "middle", "straight": "middle"}.get(pos, pos)
            if pos not in HEAD:
                notes.append(f"head {pos!r} dropped")
                continue
            step, dt = f"head {pos}", (0.9 if pos == "nod" else 0.86 if pos == "middle" else 0.6)
        elif do == "lift":
            pos = str(a.get("pos") or "").lower()
            if pos not in LIFT:
                notes.append(f"lift {pos!r} dropped")
                continue
            if on:
                held = True
                notes.append("lift held: on charger")
                continue
            step, dt = f"lift {pos}", 0.7
        elif do == "wait":
            ms = _num(a.get("ms"))
            if ms is None or ms <= 0:
                continue
            ms = min(MAX_WAIT_MS, ms)
            step, dt = f"wait {ms:.0f}", ms / 1000
        elif do == "expression":
            c = clip(a.get("name"))
            if not c:
                notes.append(f"expression {a.get('name')!r} dropped")
                continue
            step = f"face {c}"
        elif do == "trick":
            n = str(a.get("name") or "").strip().lower().replace(" ", "_")
            if n not in TRICKS:
                notes.append(f"trick {n!r} dropped")
                continue
            step, dt, dist, wheel = f"trick {n}", TRICK_S[n], TRICK_TRAVEL.get(n, 0.0), TRICKS[n]
        elif do == "volume":
            lvl = _num(a.get("level"))
            if lvl is not None:
                hub.append(("volume", int(max(1, min(5, round(lvl))))))
            continue
        elif do in ("explore", "explore_stop", "stop"):
            hub.append((do, None))
            continue
        else:
            notes.append(f"unknown {do!r} dropped")
            continue
        if wheel and on:
            held = True
            notes.append(f"{step} held: on charger")
            continue
        if step.startswith("face "):
            if len(steps) < 2 * MAX_STEPS:
                steps.append(step)
            continue
        if sum(1 for s in steps if not s.startswith("face ")) >= MAX_STEPS:
            notes.append(f"cut at {MAX_STEPS} steps")
            break
        if travel + dist > MAX_TRAVEL_MM + 0.5:
            notes.append(f"cut: travel over {MAX_TRAVEL_MM:.0f} mm")
            break
        if est + dt > MAX_PLAN_S:
            notes.append(f"cut: longer than {MAX_PLAN_S:.0f} s")
            break
        steps.append(step)
        est += dt
        travel += dist
        wheels = wheels or wheel
    if end_expr and clip(end_expr) and any(not s.startswith("face ") for s in steps):
        steps.append(f"face {clip(end_expr)}")
    moving = [s for s in steps if not s.startswith("face ")]
    plan = ("plan:" + ";".join(steps)) if moving else ""
    return {"steps": steps if moving else [], "plan": plan, "hub": hub, "notes": notes, "est_s": round(est, 1),
            "travel_mm": round(travel), "wheels": wheels, "held": held}


def gesture_plan(expr: str, on_charger: bool | None = False) -> str:
    """Small head gesture for a reply's expression (head only, also on the charger)."""
    n = expression(expr)
    if not GESTURES or not n or not EXPRESSIONS[n][1]:
        return ""
    return "plan:" + EXPRESSIONS[n][1]


# ------------------------------------------------------------ the one call
_ACTION = {
    "type": "string",
    "description": "one step: 'drive <mm>' | 'turn <deg>' | 'head up|down|middle|nod' | 'lift up|down' | "
                   "'wait <ms>' | 'expression <name>' | 'trick <name>' | 'circle <mm>' | 'explore' | 'explore_stop' | 'stop' | "
                   "'volume <1-5>'",
}
SCHEMA = {
    "type": "json_schema",
    "name": "vector_turn",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply", "expression", "expression_end", "speak", "intent", "args", "actions"],
        "properties": {
            "reply": {"type": "string"},
            "expression": {"type": "string", "enum": NAMES},
            "expression_end": {"type": ["string", "null"], "enum": NAMES + [None]},
            "speak": {"type": "string", "enum": ["before", "during", "after"]},
            "intent": {"type": ["string", "null"], "enum": INTENT_IDS + [None]},
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
            "actions": {"type": "array", "items": _ACTION},
        },
    },
}


def _intent_lines() -> str:
    import router
    return "\n".join(f"- {k}: {v}" for k, v in router.DESC.items() if k in INTENT_IDS)


RULES = (
    "\n\nYou are also Vector's body: you really can drive, turn, spin, dance, nod, move your head and lift and "
    "show feelings with your eyes - never say you can't move or dance. Every turn you return JSON: the spoken reply, the facial expression your eyes "
    "show while you say it, and optionally what to do.\n"
    "EXPRESSION: pick the emotion that really fits the reply and the situation - not always happy. Sad news -> sad, "
    "being scolded -> hurt, a joke at your expense -> suspicious or shy, a hard question -> thinking, praise -> proud, "
    "'I love you' -> love, bedtime -> sleepy, spooky -> scared, facts/time/weather -> neutral or what the fact feels "
    "like. expression_end (optional) is the face after the moves or at the end of speaking (e.g. a dance ends happy).\n"
    "Expressions: " + "; ".join(f"{k} ({v[2]})" for k, v in EXPRESSIONS.items()) + ".\n"
    "ACTIONS: only when the person asks you to move or do something physical right now. Normal conversation, "
    "questions, stories, talking ABOUT moving, past or hypothetical actions -> actions: [] and intent: null. "
    "actions is a list of short step strings, run in order (max 12 movement steps, 30 s, 2 m of driving per "
    "request; expression steps are free):\n"
    "- 'drive <mm>': signed millimetres, + forward, - backward, |mm|<=500 per step ('a bit' = 100, 'a little' = 50, "
    "'far' = 400)\n"
    "- 'turn <deg>': signed degrees, + = left / counter-clockwise, - = right; a full spin = 360; max 720 per step\n"
    "- 'head up' / 'head down' / 'head middle' / 'head nod'; 'lift up' / 'lift down'\n"
    "- 'wait <ms>' (<=5000); 'expression <name>' (change the eyes mid-sequence)\n"
    "- 'trick <name>' with name one of: " + ", ".join(TRICKS) + "\n"
    "- 'circle <diameter mm> [left|right]': a whole circle in one step (default 300, uses all 12 steps)\n"
    "- 'explore' (wander on your own), 'explore_stop', 'stop', 'volume <1-5>'\n"
    "Compose complex moves from these: a square = 4 x ('drive 200', 'turn 90'); a circle = 'circle 300'; spin around twice = 'turn 720'; a happy wiggle = 'turn 20', 'turn -40', 'turn 20' with expression "
    "happy; zigzag = 'turn 30', 'drive 100', 'turn -60', 'drive 100', 'turn 30'; look at me = 'trick look_at_me'. "
    "Closed shapes must turn 360 in total (a square: 4 x turn 90). Keep it small (you are 10 cm long and live "
    "on a desk or floor).\n"
    "speak: 'before' = say the reply, then move (default for drives and turns, so they can say stop); 'during' = "
    "talk while moving (short dances, wiggles, head moves); 'after' = move first, then say it (e.g. 'Done!').\n"
    "INTENT: only for these stock commands (with actions: []), when the request is exactly one of them; otherwise "
    "null. Their reply is built in, but still write a short reply:\n" + "{INTENTS}\n"
    "Moves are always actions (never an intent too); 'intent' is for timers, time, photos, names, volume, "
    "charger, greetings and the like. You cannot see edges: the robot's own sensors stop you at cliffs and obstacles.\n"
    "The reply is always one or two short spoken sentences in the user's language (English, German or Persian), "
    "metric units only (convert to °C and km/h, never Fahrenheit or mph).\n"
    "Examples:\n"
    "'Drive in a square' -> reply 'Okay, one square coming up!', expression determined, expression_end happy, "
    "speak before, actions ['drive 200','turn 90','drive 200','turn 90','drive 200','turn 90','drive 200','turn 90'].\n"
    "'Fahr ein Stück vor, dann dreh dich nach links und schau hoch' -> reply 'Mach ich!', expression happy, "
    "speak before, actions ['drive 100','turn 90','head up'].\n"
    "'یه دور کامل دور خودت بچرخ' -> reply 'باشه، می‌چرخم!', expression excited, speak during, actions ['turn 360'].\n"
    "'Zieh einen Kreis' -> reply 'Ich fahre einen Kreis!', expression determined, speak before, actions ['circle 300'].\n"
    "'Dance for me' / 'Tanz mal' / 'برقص' -> reply 'Watch this!', expression excited, expression_end happy, speak "
    "during, actions ['trick dance'].\n"
    "'My cat died yesterday' -> reply with sympathy, expression sad, actions [].\n"
    "'You're stupid' -> expression hurt, actions []. 'Du bist so süß' -> expression shy or love, actions [].\n"
    "'What's the weather tomorrow?' -> web search, expression neutral (or worried for a storm), actions [].\n"
    "'Can you dance?' (a question about ability) -> answer yes and you may dance: actions ['trick dance'], speak during.\n"
    "'من دیروز رقصیدم' (I danced yesterday) -> chat, expression happy, actions []."
)


def instructions(base: str) -> str:
    return base + RULES.replace("{INTENTS}", _intent_lines())


def payload(model: str, base_prompt: str, history: list, text: str, web_tool: dict | None) -> dict:
    p = {
        "model": model,
        "instructions": instructions(base_prompt),
        "input": list(history) + [{"role": "user", "content": text}],
        "max_output_tokens": MAX_TOKENS,
        "text": {"format": SCHEMA},
    }
    if web_tool:
        p["tools"] = [web_tool]
        p["tool_choice"] = "auto"
    return p


_JSON = re.compile(r"\{.*\}", re.S)


def parse(raw_text: str) -> dict | None:
    """Model output text -> validated dict (or None)."""
    if not raw_text:
        return None
    try:
        d = json.loads(raw_text)
    except json.JSONDecodeError:
        m = _JSON.search(raw_text)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(d, dict) or not isinstance(d.get("reply", ""), str):
        return None
    acts = d.get("actions") if isinstance(d.get("actions"), list) else []
    it = d.get("intent")
    if it not in INTENT_IDS:
        it = None
    sp = d.get("speak") if d.get("speak") in ("before", "during", "after") else "before"
    args = d.get("args") if isinstance(d.get("args"), dict) else {}
    return {"reply": d.get("reply") or "", "expression": expression(d.get("expression")),
            "expression_end": expression(d.get("expression_end")), "speak": sp, "intent": it,
            "args": {k: args.get(k) for k in ("duration", "level", "name")}, "actions": acts}


def ssh_text(text: str) -> bool:
    """SSH talk never reaches this model: it keeps the local/legacy path."""
    return bool(re.search(r"\bssh\b|\bs\s?s\s?h\b|es es ha|اس ?اس ?اچ|اس ?اس ?اج", text or "", re.I))


class Scheduler:
    """Deferred robot commands (plan after the speech, speech after the plan).
    cancel() drops everything still pending (voice stop, button, Stop Vector)."""

    def __init__(self) -> None:
        import threading
        self.gen = 0
        self.lock = threading.Lock()

    def later(self, delay: float, fn) -> None:
        import threading
        with self.lock:
            g = self.gen

        def run():
            with self.lock:
                if g != self.gen:
                    return
            fn()
        t = threading.Timer(max(0.0, delay), run)
        t.daemon = True
        t.start()

    def cancel(self) -> None:
        with self.lock:
            self.gen += 1


def summary(d: dict, plan: dict | None) -> str:
    p = plan or {}
    return (f"expr={d.get('expression') or 'neutral'}->{d.get('expression_end') or '-'} speak={d.get('speak')} "
            f"intent={d.get('intent') or '-'} plan={p.get('plan') or '-'} hub={p.get('hub') or '-'} "
            f"notes={p.get('notes') or '-'} est={p.get('est_s', 0)}s travel={p.get('travel_mm', 0)}mm")


def now_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
