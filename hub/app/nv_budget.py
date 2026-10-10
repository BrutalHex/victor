"""Hard budget for every NVIDIA API call, shared by all callers.

The free NVIDIA endpoint allows ~30 requests/minute and M.O has a 400-call
limit (unclear whether per day or total credits). Every request (including
retries) must first take a slot here:

    ok, why = BUDGET.take("identity question")
    if not ok: ...fall back to local-only, say nothing...

- NVIDIA_RPM (default 20): sliding 60 s window, margin under 30 rpm.
- NVIDIA_MAX_CALLS (default 400) with NVIDIA_CAP_WINDOW=day (resets at local
  midnight, HUB_TZ) or total (never resets; edit/delete the state file).
- Persisted to HUB_NVIDIA_BUDGET_FILE (default /app/data/nvidia_budget.json) so a
  hub restart does not reset the count. Every call is logged with its reason.
"""

from __future__ import annotations

import json
import os
import threading
import time


def _int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def _today(now: float) -> str:
    tz = None
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(os.environ.get("HUB_TZ") or "Europe/Berlin")
    except Exception:  # noqa: BLE001
        tz = None
    import datetime as dt

    return dt.datetime.fromtimestamp(now, tz).date().isoformat()


class Budget:
    def __init__(self, path: str | None = None, rpm: int | None = None, max_calls: int | None = None,
                 window: str | None = None, clock=time.time, day=_today) -> None:
        self.path = path if path is not None else (
            os.environ.get("HUB_NVIDIA_BUDGET_FILE")
            or ("/app/data/nvidia_budget.json" if os.path.isdir("/app/data") else "/tmp/victor-nvidia-budget.json"))
        self.rpm = max(1, rpm if rpm is not None else _int("NVIDIA_RPM", 20))
        self.max_calls = max(0, max_calls if max_calls is not None else _int("NVIDIA_MAX_CALLS", 400))
        w = (window if window is not None else os.environ.get("NVIDIA_CAP_WINDOW", "day")).strip().lower()
        self.window = "total" if w == "total" else "day"
        self.clock, self.day = clock, day
        self.lock = threading.Lock()
        self.recent: list[float] = []  # call times in the last 60 s
        self.count = 0  # calls in the current cap window
        self.total = 0  # all calls ever (this state file)
        self.period = ""  # day key when window=day
        self.denied = {"rpm": 0, "cap": 0}
        self.log: list[dict] = []  # last calls (reason, time)
        self.by_reason: dict[str, int] = {}
        self._load()

    # ----- persistence --------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.count = int(d.get("count") or 0)
        self.total = int(d.get("total") or 0)
        self.period = str(d.get("period") or "")
        self.recent = [float(t) for t in d.get("recent") or []][-100:]
        self.log = list(d.get("log") or [])[-30:]
        self.by_reason = {str(k): int(v) for k, v in (d.get("by_reason") or {}).items()}

    def _save(self) -> None:
        d = {"count": self.count, "total": self.total, "period": self.period, "window": self.window,
             "recent": self.recent, "log": self.log[-30:], "by_reason": self.by_reason}
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, self.path)
        except OSError as exc:
            print(f"nvidia budget save failed {exc!r}", flush=True)

    def _roll(self, now: float) -> None:
        self.recent = [t for t in self.recent if now - t < 60.0]
        if self.window == "day":
            key = self.day(now)
            if key != self.period:
                self.period, self.count = key, 0

    # ----- API ----------------------------------------------------------
    def remaining(self) -> int:
        with self.lock:
            self._roll(self.clock())
            return max(0, self.max_calls - self.count)

    def check(self) -> tuple[bool, str]:
        """Would a call be allowed now? (does not take a slot)"""
        with self.lock:
            now = self.clock()
            self._roll(now)
            if self.count >= self.max_calls:
                return False, "cap"
            if len(self.recent) >= self.rpm:
                return False, "rpm"
            return True, ""

    def take(self, reason: str) -> tuple[bool, str]:
        """Reserve one NVIDIA request. (False, "cap"|"rpm") = do not call."""
        with self.lock:
            now = self.clock()
            self._roll(now)
            why = ""
            if self.count >= self.max_calls:
                why = "cap"
            elif len(self.recent) >= self.rpm:
                why = "rpm"
            if why:
                self.denied[why] += 1
                print(f"nvidia budget DENY reason={reason!r} why={why} used={self.count}/{self.max_calls} "
                      f"window={self.window} last60s={len(self.recent)}/{self.rpm}", flush=True)
                return False, why
            self.recent.append(now)
            self.count += 1
            self.total += 1
            self.by_reason[reason] = self.by_reason.get(reason, 0) + 1
            self.log.append({"t": round(now, 1), "reason": reason})
            self.log = self.log[-30:]
            self._save()
            print(f"nvidia call reason={reason!r} used={self.count}/{self.max_calls} window={self.window} "
                  f"last60s={len(self.recent)}/{self.rpm} total={self.total}", flush=True)
            return True, ""

    def status(self) -> dict:
        with self.lock:
            now = self.clock()
            self._roll(now)
            return {"used": self.count, "max": self.max_calls, "remaining": max(0, self.max_calls - self.count),
                    "window": self.window, "period": self.period if self.window == "day" else "total",
                    "rpm": self.rpm, "last60s": len(self.recent), "total": self.total, "denied": dict(self.denied),
                    "by_reason": dict(self.by_reason), "last": self.log[-5:]}
