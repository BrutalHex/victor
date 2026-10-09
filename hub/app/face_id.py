"""Face-to-name on the hub with an NVIDIA-hosted VLM (OpenAI-compatible API).

Enrollment stores a few reference JPEGs per name in the faces SQLite DB
(faces.FaceDB). Recognition sends the reference gallery plus the current camera
frame and asks for strict JSON: {"person": bool, "name": <enrolled name|null>,
"confidence": 0..1}. Calls run in a background thread (motion / presence /
voice-turn kicks, rate limited); voice turns only read the cached result.

The NVIDIA key is read from the environment (HUB_FACE_API_KEY_VAR names the
variable, default NVIDIA_API_KEY) and is never logged.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"

SYSTEM = (
    "You are a careful face-matching component for a home robot. You compare the person in the "
    "CURRENT camera frame with labelled REFERENCE photos of enrolled people. Only answer with a name "
    "if the same person is clearly visible and matches a reference; otherwise use null. Never guess, "
    "never invent names, never use a name that is not in the reference list. Reply with one JSON "
    'object only, no prose: {"person": true|false, "name": <reference name or null>, '
    '"confidence": <0..1>}.'
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def clean_name(name: str) -> str:
    """Names go into prompts: printable, single line, short."""
    name = re.sub(r"[\x00-\x1f\x7f<>{}\[\]\"`]", "", str(name or "")).strip()
    return re.sub(r"\s+", " ", name)[:40]


_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_result(text: str, names: list[str]) -> dict:
    """Model output -> {"person", "name", "confidence"}; tolerant of reasoning
    text, code fences and trailing prose. Unknown names become None."""
    out = {"person": False, "name": None, "confidence": 0.0}
    if not text:
        return out
    t = _THINK.sub("", text)
    if "</think>" in t:  # opening tag eaten by the template
        t = t.rsplit("</think>", 1)[1]
    objs = re.findall(r"\{[^{}]*\}", t, re.S)
    data = None
    for raw in reversed(objs):
        try:
            data = json.loads(raw)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(data, dict):
        return out
    person = data.get("person")
    out["person"] = bool(person) if isinstance(person, bool) else str(person).lower() == "true"
    try:
        out["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        out["confidence"] = 0.0
    name = data.get("name")
    if isinstance(name, str) and name.strip() and name.strip().lower() not in ("null", "none", "unknown"):
        canon = {n.lower(): n for n in names}
        out["name"] = canon.get(clean_name(name).lower())
        if out["name"]:
            out["person"] = True
    if not out["name"]:
        out["confidence"] = min(out["confidence"], 1.0) if out["person"] else 0.0
    return out


def shrink(jpeg: bytes, max_side: int = 512, quality: int = 80) -> bytes:
    """Keep requests small: downscale to max_side and re-encode."""
    try:
        from PIL import Image  # type: ignore

        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    except Exception:
        return jpeg


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()


def build_payload(model: str, frame: bytes, refs: list[tuple[str, bytes]]) -> dict:
    names = sorted({n for n, _ in refs})
    content: list[dict] = [{
        "type": "text",
        "text": f"Enrolled people: {', '.join(names) if names else '(none)'}. "
                f"{len(refs)} reference photo(s) follow, each labelled with its name.",
    }]
    for i, (name, jpeg) in enumerate(refs, 1):
        content.append({"type": "text", "text": f"REFERENCE {i}: {name}"})
        content.append({"type": "image_url", "image_url": {"url": data_url(jpeg)}})
    content.append({"type": "text", "text": "CURRENT camera frame:"})
    content.append({"type": "image_url", "image_url": {"url": data_url(frame)}})
    content.append({
        "type": "text",
        "text": 'Is a person visible in the CURRENT frame, and is it one of the enrolled people? '
                'Answer only with JSON: {"person": true|false, "name": "<enrolled name>" or null, '
                '"confidence": 0..1}. Use null unless you are confident it is the same person.',
    })
    return {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        "max_tokens": 200,
        "temperature": 0.0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},  # no reasoning tokens
    }


def response_text(data: dict) -> str:
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(msg.get("content") or "")  # reasoning_content is ignored on purpose


class FaceID:
    def __init__(self, db=None) -> None:
        self.db = db
        self.base = (os.environ.get("HUB_FACE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = os.environ.get("HUB_FACE_MODEL") or DEFAULT_MODEL
        self.key_var = os.environ.get("HUB_FACE_API_KEY_VAR") or "NVIDIA_API_KEY"
        self.key = os.environ.get(self.key_var, "")
        self.timeout = _env_float("HUB_FACE_TIMEOUT", 25)
        self.min_gap = _env_float("HUB_FACE_MIN_GAP_S", 4)       # never call faster than this
        self.present_every = _env_float("HUB_FACE_PRESENT_S", 10)  # re-check while someone is there
        self.idle_every = _env_float("HUB_FACE_IDLE_S", 60)       # background check with no motion
        self.min_conf = _env_float("HUB_FACE_MIN_CONF", 0.7)
        self.max_refs = int(_env_float("HUB_FACE_MAX_REFS", 6))
        self.enabled = os.environ.get("HUB_FACE_ID", "1").strip().lower() not in ("0", "false", "off", "no")
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.frame = b""
        self.frame_t = 0.0
        self.thumb: list[int] | None = None
        self.motion_t = 0.0
        self.kick_t = 0.0
        self.busy = False
        self.calls = 0
        self.errors = 0
        self.last_call_t = 0.0
        self.result = {"person": False, "name": None, "confidence": 0.0, "t": 0.0, "ms": 0, "error": ""}
        self.retries = int(_env_float("HUB_FACE_RETRIES", 2))  # extra tries on 429/5xx (shared free endpoint)
        self.retry_s = _env_float("HUB_FACE_RETRY_S", 1.5)
        self.sleep = time.sleep
        self.post = self._post  # tests replace this
        self.on_result = None  # callable(result) after each successful call

    # ----- inputs -------------------------------------------------------
    def ready(self) -> bool:
        return self.enabled and bool(self.key) and self.db is not None

    def on_frame(self, jpeg: bytes) -> None:
        moved = self._motion(jpeg)
        with self.lock:
            self.frame, self.frame_t = jpeg, time.time()
            if moved:
                self.motion_t = self.frame_t
        if moved:
            self.wake.set()

    def kick(self) -> None:
        """Voice turn starting: refresh soon if the cached result is old."""
        with self.lock:
            self.kick_t = time.time()
        self.wake.set()

    def _motion(self, jpeg: bytes) -> bool:
        try:
            from PIL import Image  # type: ignore

            im = Image.open(io.BytesIO(jpeg)).convert("L").resize((16, 12))
            thumb = list(im.getdata())
        except Exception:
            return False
        prev, self.thumb = self.thumb, thumb
        if prev is None:
            return True
        diff = sum(abs(a - b) for a, b in zip(prev, thumb)) / len(thumb)
        return diff > 6.0

    # ----- scheduling ---------------------------------------------------
    def due(self, now: float) -> bool:
        with self.lock:
            if not self.frame or now - self.frame_t > 5:
                return False
            if now - self.last_call_t < self.min_gap:
                return False
            r = self.result
            age = now - (r.get("t") or 0)
            if self.kick_t > self.last_call_t and age > 8:
                return True
            if self.motion_t > self.last_call_t:
                return True
            if r.get("person") and age >= self.present_every:
                return True
            return age >= self.idle_every

    def loop(self) -> None:
        while True:
            self.wake.wait(1.0)
            self.wake.clear()
            if not self.ready():
                continue
            if not self.db.refs(1):
                continue  # nobody enrolled: no calls
            if self.due(time.time()):
                self.recognize_now()

    # ----- the call -----------------------------------------------------
    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def recognize(self, frame: bytes, refs: list[tuple[str, bytes]]) -> dict:
        t0 = time.time()
        res = {"person": False, "name": None, "confidence": 0.0, "error": "", "raw": ""}
        try:
            payload = build_payload(self.model, shrink(frame, 640), [(n, shrink(j, 384)) for n, j in refs])
            data = None
            for attempt in range(self.retries + 1):
                try:
                    data = self.post(payload)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.retries:
                        raise
                    if time.time() - t0 + self.retry_s * (attempt + 1) > self.timeout:
                        raise
                    self.sleep(self.retry_s * (attempt + 1))
            text = response_text(data)
            res["raw"] = text[:300]
            res.update(parse_result(text, [n for n, _ in refs]))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode(errors="replace")[:200]
            except OSError:
                pass
            res["error"] = f"http {exc.code} {body}"
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            res["error"] = f"{type(exc).__name__}: {exc}"[:200]
        res["ms"] = int((time.time() - t0) * 1000)
        return res

    def recognize_now(self) -> dict:
        with self.lock:
            if self.busy:
                return dict(self.result)
            self.busy = True
            frame = self.frame
            self.last_call_t = time.time()
        try:
            refs = self.db.refs(self.max_refs)
            res = self.recognize(frame, refs)
            res["t"] = time.time()
            with self.lock:
                self.calls += 1
                if res["error"]:
                    self.errors += 1
                    self.result["error"] = res["error"]
                else:
                    self.result = res
            if not res["error"] and self.on_result:
                try:
                    self.on_result(res)
                except Exception as exc:  # noqa: BLE001
                    print(f"face id on_result failed {exc!r}", flush=True)
            print(
                f"face id person={res['person']} name={res['name']!r} conf={res['confidence']:.2f} "
                f"ms={res['ms']} refs={len(refs)} err={res['error'][:120]!r}",
                flush=True,
            )
            return res
        finally:
            with self.lock:
                self.busy = False

    def wait_fresh(self, max_age: float, timeout: float) -> None:
        """Voice turn about identity: wait briefly for an in-flight or new result."""
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                age = time.time() - (self.result.get("t") or 0)
                busy = self.busy
            if age <= max_age and not busy:
                return
            time.sleep(0.1)

    # ----- outputs ------------------------------------------------------
    def present(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            r = dict(self.result)
            calls, errors = self.calls, self.errors
        t = r.get("t") or 0
        return {
            "present_name": r.get("name"),
            "present_person": bool(r.get("person")),
            "confidence": round(float(r.get("confidence") or 0), 2),
            "age_s": round(now - t, 1) if t else None,
            "ms": r.get("ms", 0),
            "calls": calls,
            "errors": errors,
            "error": r.get("error", ""),
            "enabled": self.ready(),
            "model": self.model,
        }


# ----- prompt rules (C) ---------------------------------------------------
IDENTITY_EXAMPLES = (
    "\"what's my name\", \"who am I\", \"do you know me\", \"wie heiße ich\", \"wer bin ich\", "
    "\"kennst du mich\", \"اسم من چیه\", \"من کی هستم\", \"منو میشناسی\""
)

_IDENTITY_RE = re.compile(
    r"(my name|who am i|do you (know|recogni[sz]e) me|know who i am|"
    r"wie hei(ß|ss)e ich|wer bin ich|kennst du mich|mein name|"
    r"اسم\s*من|اسمم|من\s*کی\s*هستم|منو\s*می\s*?شناسی|میشناسی\s*منو)",
    re.I,
)


def is_identity_question(text: str) -> bool:
    return bool(_IDENTITY_RE.search(text or ""))


def person_context(p: dict, min_conf: float = 0.7, fresh_s: float = 30.0) -> str:
    """System-prompt section about who is in front of the robot."""
    name = clean_name(p.get("present_name") or "")
    age = p.get("age_s")
    fresh = age is not None and age <= fresh_s
    rule = (
        f"For questions like {IDENTITY_EXAMPLES} (in English, German or Persian), answer in the user's language. "
        "Never guess or invent a name, and never use a name that is not given here."
    )
    if name and fresh and float(p.get("confidence") or 0) >= min_conf:
        return (
            f"Camera: the person in front of you is {name} (recognised {age:.0f} s ago). "
            f"If they ask their name or who they are, tell them they are {name}. "
            f"You may address them as {name}, but don't overuse it. " + rule
        )
    if fresh and p.get("present_person"):
        return (
            "Camera: you can see a person but you do not recognise them. If they ask their name or who they "
            "are, say honestly that you don't recognise them yet and offer to learn their face: they can "
            "enroll on the hub's faces page. " + rule
        )
    return (
        "Camera: you cannot see or recognise anyone right now. If asked their name or who they are, say "
        "honestly that you can't see or recognise them at the moment and offer to learn their face "
        "(enrollment on the hub's faces page). " + rule
    )
