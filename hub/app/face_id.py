"""Face-to-name on the hub with an NVIDIA-hosted VLM (OpenAI-compatible API).

Enrollment stores a few reference JPEGs per name in the faces SQLite DB
(faces.FaceDB). Recognition sends the reference gallery plus the current camera
frame and asks for strict JSON: {"person": bool, "name": <enrolled name|null>,
"confidence": 0..1}. Nothing runs in the background: the camera frame only
leaves the hub when a voice turn is an identity question ("what's my name",
"wie heiße ich", "اسم من چیه", ...) or when the user clicks enroll; that turn
makes exactly one call (identify) and the result goes into that turn's prompt.

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
    "You are a strict face-verification component for a home robot. You compare the face in the "
    "CURRENT camera frame with labelled REFERENCE photos of enrolled people. A name may only be given "
    "when the CURRENT frame shows a human face clearly enough to identify (not a silhouette, the back of "
    "a head, or a tiny/blurred figure) AND that face is the same individual as in that name's reference "
    "photos (same facial features, not just similar clothes, room, lighting or pose). References that "
    "do not show a human face can never match. When in doubt use null: a wrong name is much worse than "
    "no name. Never guess, never invent names, never use a name that is not in the reference list. "
    'Reply with one JSON object only, no prose: {"person": true|false, "face_visible": true|false, '
    '"name": <reference name or null>, "confidence": <0..1>}.'
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
    out = {"person": False, "face_visible": False, "name": None, "confidence": 0.0}
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
    fv = data.get("face_visible")
    if fv is None:
        out["face_visible"] = out["person"]  # older/short answers: assume the person's face counts
    else:
        out["face_visible"] = fv is True or str(fv).lower() == "true"
    if not out["face_visible"]:
        name = None  # no identifiable face, no name
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
        "text": 'Is a person visible in the CURRENT frame, is their face clearly visible, and is it the same '
                'individual as one of the enrolled people? Answer only with JSON: {"person": true|false, '
                '"face_visible": true|false, "name": "<enrolled name>" or null, "confidence": 0..1}. '
                'Use null unless you are confident it is the same face.',
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
        self.frame_max_age = _env_float("HUB_FACE_FRAME_MAX_AGE_S", 5)  # older frame = can't see
        self.min_conf = _env_float("HUB_FACE_MIN_CONF", 0.7)
        self.max_refs = int(_env_float("HUB_FACE_MAX_REFS", 6))
        self.enabled = os.environ.get("HUB_FACE_ID", "1").strip().lower() not in ("0", "false", "off", "no")
        self.lock = threading.Lock()
        self.frame = b""
        self.frame_t = 0.0
        self.busy = False
        self.calls = 0  # identity-question calls
        self.enroll_calls = 0  # explicit enroll checks (POST /faces)
        self.errors = 0
        self.last_call_t = 0.0
        self.result = {"person": False, "name": None, "confidence": 0.0, "t": 0.0, "ms": 0, "error": ""}
        self.retries = int(_env_float("HUB_FACE_RETRIES", 4))  # extra tries on 429/5xx (shared free endpoint)
        self.retry_s = _env_float("HUB_FACE_RETRY_S", 1.5)
        self.sleep = time.sleep
        self.post = self._post  # tests replace this

    # ----- inputs -------------------------------------------------------
    def ready(self) -> bool:
        return self.enabled and bool(self.key) and self.db is not None

    def on_frame(self, jpeg: bytes) -> None:
        """Remember the newest face frame (local only; nothing is sent)."""
        with self.lock:
            self.frame, self.frame_t = jpeg, time.time()

    def latest(self, now: float | None = None) -> bytes:
        now = time.time() if now is None else now
        with self.lock:
            if self.frame and now - self.frame_t <= self.frame_max_age:
                return self.frame
        return b""

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
        res = {"person": False, "face_visible": False, "name": None, "confidence": 0.0, "error": "", "raw": ""}
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

    def identify(self) -> dict:
        """Identity question: one NVIDIA call on the freshest face frame.
        No call without key, enrollment or a fresh frame; the reason is in "skip"."""
        res = {"person": False, "face_visible": False, "name": None, "confidence": 0.0,
               "error": "", "raw": "", "ms": 0, "skip": "", "t": time.time()}
        if not self.ready():
            res["skip"] = "off"
            return res
        refs = self.db.refs(self.max_refs)
        frame = self.latest()
        if not refs:
            res["skip"] = "nobody enrolled"
        elif not frame:
            res["skip"] = "no fresh camera frame"
        else:
            with self.lock:
                self.busy = True
            try:
                res.update(self.recognize(frame, refs))
                res["t"] = time.time()
            finally:
                with self.lock:
                    self.busy = False
                    self.calls += 1
                    if res["error"]:
                        self.errors += 1
        with self.lock:
            self.result = dict(res)
        print(
            f"face id (identity question) person={res['person']} name={res['name']!r} "
            f"conf={res['confidence']:.2f} ms={res['ms']} refs={len(refs)} skip={res['skip']!r} "
            f"err={res['error'][:120]!r}",
            flush=True,
        )
        return res

    # ----- outputs ------------------------------------------------------
    def present(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self.lock:
            r = dict(self.result)
            calls, errors, enroll_calls = self.calls, self.errors, self.enroll_calls
        t = r.get("t") or 0
        return {
            "present_name": r.get("name"),
            "present_person": bool(r.get("person")),
            "confidence": round(float(r.get("confidence") or 0), 2),
            "age_s": round(now - t, 1) if t else None,
            "ms": r.get("ms", 0),
            "calls": calls,
            "enroll_calls": enroll_calls,
            "errors": errors,
            "error": r.get("error", ""),
            "skip": r.get("skip", ""),
            "enabled": self.ready(),
            "trigger": "identity questions and enroll only",
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


NO_CONTEXT = (
    "Camera: you have no identity information for this turn. Never guess or invent a person's name; "
    "if someone asks who they are, say you can check when they ask \"what's my name?\"."
)


def person_context(res: dict, min_conf: float = 0.7) -> str:
    """System-prompt section for an identity question, from this turn's identify() result."""
    name = clean_name(res.get("name") or res.get("present_name") or "")
    rule = (
        f"This is an identity question like {IDENTITY_EXAMPLES}; answer in the user's language "
        "(English, German or Persian). Never guess or invent a name, and never use a name that is not given here."
    )
    if name and not res.get("error") and float(res.get("confidence") or 0) >= min_conf:
        return (
            f"Camera (checked just now): the person in front of you is {name}. "
            f"Tell them they are {name}. " + rule
        )
    if res.get("error"):
        return (
            "Camera: the face check failed just now (service busy or unreachable). Say honestly that you "
            "couldn't check right now and ask them to try again in a moment. " + rule
        )
    if res.get("skip") == "nobody enrolled":
        return (
            "Camera: nobody has been enrolled yet, so you cannot recognise anyone. Say so honestly and offer "
            "to learn their face: they can enroll on the hub's face page. " + rule
        )
    if res.get("skip"):
        return (
            "Camera: you cannot see anyone right now. Say honestly that you can't see or recognise them at the "
            "moment and offer to learn their face (enrollment on the hub's face page). " + rule
        )
    if res.get("person_visible", res.get("person")) or res.get("present_person"):
        return (
            "Camera: you can see a person but you do not recognise them (or their face is not clearly visible). "
            "Say honestly that you don't recognise them yet; they can face the robot and ask again, or enroll on "
            "the hub's face page. " + rule
        )
    return (
        "Camera: you cannot see anyone right now. Say honestly that you can't see or recognise them at the "
        "moment and offer to learn their face (enrollment on the hub's face page). " + rule
    )
