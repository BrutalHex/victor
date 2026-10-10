"""Wake-word conversation session: ASLEEP <-> AWAKE.

ASLEEP (default after hub start): utterances only reach the local wake
detector; nothing goes to OpenAI. "Hey Vector" -> AWAKE. Every utterance in
AWAKE runs the normal pipeline with this session's history as context.
"Stop Vector" (or HUB_SESSION_IDLE_S of silence, if set) -> ASLEEP, history
cleared. HUB_WAKE=0 disables all of this (always listening, as before).
"""
from __future__ import annotations

import os
import threading
import time

ASLEEP, AWAKE = "asleep", "awake"


def _env_on(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


class Session:
    def __init__(self, enabled: bool | None = None, idle_s: float | None = None, max_turns: int | None = None,
                 clock=time.time) -> None:
        self.enabled = _env_on("HUB_WAKE", "1") if enabled is None else enabled
        self.idle_s = float(os.environ.get("HUB_SESSION_IDLE_S", "0") or 0) if idle_s is None else idle_s
        self.max_turns = int(os.environ.get("HUB_SESSION_HISTORY", "8")) if max_turns is None else max_turns
        self.clock = clock
        self.lock = threading.Lock()
        self.state = ASLEEP
        self.since = clock()
        self.last = clock()
        self.history: list[tuple[str, str]] = []
        self.lang = ""
        self.wakes = self.ends = self.turns = self.timeouts = 0
        self.last_end = ""

    def listening(self) -> bool:
        """True: utterances go to the full (OpenAI) pipeline."""
        if not self.enabled:
            return True
        self.check_idle()
        return self.state == AWAKE

    def wake(self, why: str = "wake") -> bool:
        with self.lock:
            if self.state == AWAKE:
                self.last = self.clock()
                return False
            self.state, self.since, self.last = AWAKE, self.clock(), self.clock()
            self.history = []
            self.wakes += 1
        print(f"session awake why={why}", flush=True)
        return True

    def sleep(self, why: str = "stop") -> bool:
        with self.lock:
            if self.state == ASLEEP:
                return False
            self.state, self.since = ASLEEP, self.clock()
            self.history = []
            self.ends += 1
            self.last_end = why
        print(f"session asleep why={why}", flush=True)
        return True

    def check_idle(self) -> bool:
        """Sleep after idle_s without a turn (0 = never). True if it just slept."""
        if not self.enabled or self.idle_s <= 0 or self.state != AWAKE:
            return False
        if self.clock() - self.last < self.idle_s:
            return False
        self.timeouts += 1
        return self.sleep("idle")

    def touch(self) -> None:
        self.last = self.clock()

    def add(self, user: str, reply: str, lang: str = "") -> None:
        if not self.enabled or self.state != AWAKE or not user:
            return
        with self.lock:
            self.history.append((user, reply))
            self.history = self.history[-self.max_turns:]
            self.turns += 1
            self.last = self.clock()
            if lang:
                self.lang = lang

    def messages(self) -> list[dict]:
        """Chat history for this session (empty when the wake word is off)."""
        if not self.enabled:
            return []
        with self.lock:
            out = []
            for u, r in self.history:
                out.append({"role": "user", "content": u})
                if r:
                    out.append({"role": "assistant", "content": r})
            return out

    def status(self) -> dict:
        now = self.clock()
        return {"enabled": self.enabled, "state": self.state if self.enabled else "always",
                "for_s": round(now - self.since, 1), "idle_s": round(now - self.last, 1),
                "idle_timeout_s": self.idle_s, "history_turns": len(self.history), "wakes": self.wakes,
                "ends": self.ends, "timeouts": self.timeouts, "turns": self.turns, "last_end": self.last_end}
