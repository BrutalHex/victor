"""Who is in front of Vector: local scan, result cache, NVIDIA only when
uncertain, and the spontaneous "Hi Mohammad!" greeting.

Flow per scanned frame (face_local does detection + embedding on the hub):

  clear face -> cache hit (same embedding seen < HUB_FACE_CACHE_S ago)? use it
             -> local match: sure -> name; unknown -> nobody enrolled
             -> uncertain -> NVIDIA (shared nv_budget), but in the background
                only if that would lead to a greeting (candidate not greeted
                within the cooldown, greeting allowed right now), at most
                HUB_FACE_BG_MAX_PER_HOUR, and never when fewer than
                HUB_FACE_BG_RESERVE calls are left. Otherwise local-only.

Greeting: a known face seen in HUB_GREET_STABLE consecutive scans, not greeted
for HUB_GREET_COOLDOWN_S (2 h), Vector not speaking/thinking and no voice turn
in the last HUB_GREET_QUIET_S, then a random 0.8-2.5 s pause and a re-check.
Awake: say "Hi <name>!" with the happy hello eyes. Asleep (HUB_GREET_ASLEEP):
"look" = look up + happy eyes, no speech (default); "speak" = same as awake;
"off" = nothing. Unknown faces: nothing, unless HUB_GREET_UNKNOWN=1 (awake
only, once per HUB_GREET_UNKNOWN_COOLDOWN_S): "I don't think we've met..."
"""

from __future__ import annotations

import json
import os
import random
import threading
import time


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _on(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "off", "no", "")


class Watcher:
    def __init__(self, local, faceid=None, budget=None, db=None, clock=time.time, rng=random.random,
                 greet_path: str | None = None) -> None:
        self.local, self.faceid, self.budget, self.db = local, faceid, budget, db
        self.clock, self.rng = clock, rng
        self.cache_s = _f("HUB_FACE_CACHE_S", 600)
        self.cache_sim = _f("HUB_FACE_CACHE_SIM", 0.60)
        self.bg_nvidia = _on("HUB_FACE_BG_NVIDIA", "1")
        self.bg_max_h = int(_f("HUB_FACE_BG_MAX_PER_HOUR", 6))
        self.bg_reserve = int(_f("HUB_FACE_BG_RESERVE", 50))
        self.confirm_first = _on("HUB_FACE_CONFIRM_FIRST", "0")
        self.learn_conf = _f("HUB_FACE_LEARN_CONF", 0.85)
        self.min_conf = _f("HUB_FACE_MIN_CONF", 0.7)
        self.greet_on = _on("HUB_GREET", "1")
        self.cooldown = _f("HUB_GREET_COOLDOWN_S", 7200)
        self.stable_n = int(_f("HUB_GREET_STABLE", 2))
        self.quiet_s = _f("HUB_GREET_QUIET_S", 20)
        self.asleep_mode = (os.environ.get("HUB_GREET_ASLEEP", "look").strip().lower() or "look")
        self.unknown_on = _on("HUB_GREET_UNKNOWN", "0")
        self.unknown_cooldown = _f("HUB_GREET_UNKNOWN_COOLDOWN_S", 21600)
        self.unknown_stable = int(_f("HUB_GREET_UNKNOWN_STABLE", 4))
        self.scan_s = _f("HUB_FACE_SCAN_S", 1.0)  # while a face was seen recently
        self.idle_scan_s = _f("HUB_FACE_IDLE_SCAN_S", 2.0)  # nobody around
        self.lock = threading.Lock()
        self.cache: list[dict] = []
        self.bg_calls: list[float] = []
        self.greet_path = greet_path if greet_path is not None else (
            os.environ.get("HUB_GREET_FILE")
            or ("/app/data/face_greet.json" if os.path.isdir("/app/data") else "/tmp/victor-face-greet.json"))
        self.greeted: dict[str, float] = {}
        self.unknown_asked = 0.0
        self.streak = {"name": None, "n": 0, "unknown": 0}
        self.last_face_t = 0.0
        self.last_scan_t = 0.0
        self.last = {}  # last scan summary for /status
        self.stats = {"scans": 0, "faces": 0, "cache_hits": 0, "local_sure": 0, "local_unsure": 0,
                      "local_unknown": 0, "nvidia": 0, "nvidia_skipped": 0, "learned": 0, "greets": 0, "asks": 0}
        self._load_greeted()

    # ----- persistence of greet times -----------------------------------
    def _load_greeted(self) -> None:
        try:
            with open(self.greet_path) as f:
                d = json.load(f)
            self.greeted = {str(k): float(v) for k, v in (d.get("greeted") or {}).items()}
            self.unknown_asked = float(d.get("unknown_asked") or 0)
        except (OSError, ValueError, AttributeError):
            pass

    def _save_greeted(self) -> None:
        try:
            parent = os.path.dirname(self.greet_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.greet_path, "w") as f:
                json.dump({"greeted": self.greeted, "unknown_asked": self.unknown_asked}, f)
        except OSError:
            pass

    # ----- scan pacing --------------------------------------------------
    def due(self, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        period = self.scan_s if now - self.last_face_t < 30 else self.idle_scan_s
        return now - self.last_scan_t >= period

    # ----- cache --------------------------------------------------------
    def cache_get(self, emb, now: float) -> dict | None:
        with self.lock:
            self.cache = [c for c in self.cache if now - c["t"] < self.cache_s]
            best, sim = None, self.cache_sim
            for c in self.cache:
                s = float(self.local.np.dot(emb, c["emb"])) if self.local is not None else 0.0
                if s >= sim:
                    best, sim = c, s
            return dict(best) if best else None

    def cache_put(self, emb, res: dict, now: float) -> None:
        with self.lock:
            self.cache.append({"emb": emb, "name": res.get("name"), "conf": res.get("confidence", 0.0),
                               "via": res.get("via", ""), "level": res.get("level", ""), "t": now})
            self.cache = self.cache[-50:]

    def clear_cache(self) -> None:
        with self.lock:
            self.cache = []

    # ----- greeting policy ----------------------------------------------
    def greet_mode(self, ctx: dict) -> str:
        """'speak' | 'look' | 'off' for the robot's state right now."""
        if not self.greet_on:
            return "off"
        if ctx.get("awake", True):
            return "speak"
        return self.asleep_mode if self.asleep_mode in ("speak", "look") else "off"

    def quiet(self, ctx: dict) -> bool:
        """Not speaking, not thinking, no voice turn running or just finished."""
        return not (ctx.get("busy") or ctx.get("speaking") or ctx.get("thinking")) and \
            float(ctx.get("since_turn_s", 1e9)) >= self.quiet_s

    def greeted_recently(self, name: str | None, now: float) -> bool:
        return bool(name) and now - self.greeted.get(name.lower(), 0.0) < self.cooldown

    def mark_greeted(self, name: str, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.greeted[name.lower()] = now
        self.stats["greets"] += 1
        self._save_greeted()

    # ----- NVIDIA (uncertain only) ----------------------------------------
    def _bg_allowed(self, now: float) -> tuple[bool, str]:
        self.bg_calls = [t for t in self.bg_calls if now - t < 3600]
        if not self.bg_nvidia:
            return False, "background NVIDIA off"
        if len(self.bg_calls) >= self.bg_max_h:
            return False, "background hourly limit"
        if self.budget is not None and self.budget.remaining() <= self.bg_reserve:
            return False, "budget reserve"
        return True, ""

    def ask_nvidia(self, frame: bytes, cand: str | None, reason: str) -> dict | None:
        """One budgeted NVIDIA check. None when not possible (no key, budget)."""
        if self.faceid is None or not self.faceid.ready() or self.db is None:
            return None
        refs = self.db.refs(self.faceid.max_refs)
        if not refs:
            return None
        res = self.faceid.recognize(frame, refs, reason=reason)
        if res.get("skip") == "budget":
            return None
        self.stats["nvidia"] += 1
        return res

    def resolve(self, emb, frame: bytes, purpose: str, ctx: dict | None = None, now: float | None = None) -> dict:
        """Name for one face embedding. purpose: 'identity' (a voice question:
        NVIDIA whenever uncertain) or 'greet' (background: NVIDIA only if it
        could lead to a greeting). -> {"name", "confidence", "via", "level", "score"}"""
        now = self.clock() if now is None else now
        ctx = ctx or {}
        hit = self.cache_get(emb, now)
        if hit and (hit["via"] != "local-unsure" or purpose == "greet"):
            self.stats["cache_hits"] += 1
            return {"name": hit["name"], "confidence": hit["conf"], "via": "cache:" + hit["via"],
                    "level": hit["level"], "score": None}
        m = self.local.match(emb)
        out = {"name": None, "confidence": 0.0, "via": "local", "level": m["level"], "score": m["score"],
               "candidate": m["name"]}
        if m["level"] == "sure" and not (self.confirm_first and purpose == "greet"
                                         and not self.greeted_recently(m["name"], now)):
            self.stats["local_sure"] += 1
            out.update(name=m["name"], confidence=round(min(0.99, 0.7 + m["score"] / 2), 2))
            self.cache_put(emb, out, now)
            return out
        if m["level"] in ("unknown", "empty"):
            self.stats["local_unknown"] += 1
            self.cache_put(emb, out, now)
            return out
        # uncertain (or "sure" with confirm-first on)
        self.stats["local_unsure"] += 1
        why = ""
        if purpose == "greet":
            if self.greeted_recently(m["name"], now):
                why = "already greeted"
            elif self.greet_mode(ctx) == "off" or not self.quiet(ctx):
                why = "would not greet now"
            else:
                ok, why = self._bg_allowed(now)
                why = "" if ok else why
        if not why:
            res = self.ask_nvidia(frame, m["name"], "greeting check" if purpose == "greet" else "identity question")
            if purpose == "greet" and res is not None:
                self.bg_calls.append(now)
            if res is not None:
                if res.get("error"):
                    out.update(via="nvidia-error", error=res["error"])
                    return out  # not cached: try again next time
                name, conf = res.get("name"), float(res.get("confidence") or 0)
                out.update(via="nvidia", confidence=round(conf, 2), person=res.get("person"))
                if name and conf >= self.min_conf:
                    out["name"] = name
                    if name == m["name"] and conf >= self.learn_conf and self.db is not None:
                        self.learn(name, emb)
                self.cache_put(emb, out, now)
                return out
            why = "no NVIDIA (key/budget)"
        self.stats["nvidia_skipped"] += 1
        out.update(via="local-unsure", skip=why)
        if purpose == "greet" and why != "would not greet now":
            self.cache_put(emb, out, now)
        return out

    def learn(self, name: str, emb) -> None:
        """NVIDIA confirmed an uncertain local match: keep that embedding as an
        extra local reference (capped per name), so next time it is local."""
        try:
            self.db.add_embedding(name, emb.astype("float32").tobytes(), "nvidia-confirmed")
            self.local.load_extra(self.db.embeddings())
            self.stats["learned"] += 1
            print(f"face learned extra reference for {name!r}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"face learn failed {exc!r}", flush=True)

    # ----- one background scan --------------------------------------------
    def scan(self, frame: bytes, ctx: dict, now: float | None = None) -> dict:
        """-> {"greet": name} | {"ask": True} | {} for this frame."""
        now = self.clock() if now is None else now
        self.last_scan_t = now
        self.stats["scans"] += 1
        faces, clear, img = self.local.detect(frame)
        if faces:
            self.last_face_t = now
        if not clear:
            self.streak.update(name=None, n=0, unknown=0)
            self.last = {"t": round(now, 1), "faces": len(faces), "clear": 0}
            return {}
        self.stats["faces"] += 1
        face = clear[0]  # biggest clear face
        emb = self.local.embed(img, face)
        r = self.resolve(emb, frame, "greet", ctx, now)
        self.last = {"t": round(now, 1), "faces": len(faces), "clear": len(clear), "name": r.get("name"),
                     "via": r.get("via"), "score": r.get("score"), "level": r.get("level")}
        name = r.get("name")
        if name:
            if self.streak["name"] == name:
                self.streak["n"] += 1
            else:
                self.streak.update(name=name, n=1)
            self.streak["unknown"] = 0
            if self.streak["n"] >= self.stable_n and not self.greeted_recently(name, now) \
                    and self.greet_mode(ctx) != "off" and self.quiet(ctx):
                return {"greet": name}
            return {}
        self.streak.update(name=None, n=0)
        if r.get("level") in ("unknown", "empty"):
            self.streak["unknown"] += 1
            if (self.unknown_on and ctx.get("awake") and self.quiet(ctx)
                    and self.streak["unknown"] >= self.unknown_stable
                    and now - self.unknown_asked >= self.unknown_cooldown):
                return {"ask": True}
        return {}

    def mark_asked(self, now: float | None = None) -> None:
        self.unknown_asked = self.clock() if now is None else now
        self.stats["asks"] += 1
        self._save_greeted()

    def delay(self) -> float:
        return 0.8 + 1.7 * self.rng()

    def still_there(self, name: str, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        return self.last.get("name") == name and now - float(self.last.get("t") or 0) < 4.0

    def status(self) -> dict:
        now = self.clock()
        return {"stats": dict(self.stats), "last": dict(self.last), "cache": len(self.cache),
                "greet": {"on": self.greet_on, "asleep": self.asleep_mode, "cooldown_s": self.cooldown,
                          "unknown": self.unknown_on,
                          "recent": {k: round(now - v) for k, v in self.greeted.items() if now - v < self.cooldown}},
                "bg_nvidia_last_hour": len([t for t in self.bg_calls if now - t < 3600]),
                "bg_max_per_hour": self.bg_max_h}
