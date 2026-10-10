#!/usr/bin/env python3
"""Real OpenAI check of the one-call brain, text only (nothing is sent to the
robot). Run inside the hub container, where the key lives:

    docker exec hub python brain_eval.py            # brain only
    docker exec hub python brain_eval.py --compare  # + old router+chat timing

Prints, per prompt: model ms, expression(s), speak mode, intent, the clamped
plan, and the reply. Never prints the key or any other .env value."""

from __future__ import annotations

import os
import statistics
import sys
import time

os.environ.setdefault("HUB_FACE_LOCAL", "0")

import brain  # noqa: E402
import router  # noqa: E402
from voice import Voice  # noqa: E402

PROMPTS = [
    ("en", "Drive in a square"),
    ("en", "Spin around twice"),
    ("en", "Go forward a bit, then turn left and look up"),
    ("en", "Dance for me"),
    ("en", "Do a happy wiggle"),
    ("en", "My cat died yesterday"),
    ("en", "You're a stupid robot"),
    ("en", "What's the weather in Berlin tomorrow?"),
    ("en", "Tell me a joke about robots"),
    ("de", "Zieh einen Kreis"),
    ("de", "Fahr ein Stück zurück und schau mich dann an"),
    ("de", "Ich habe heute meinen Job verloren"),
    ("de", "Was ist die Hauptstadt von Australien?"),
    ("fa", "یه مربع بکش و برگرد"),
    ("fa", "دو بار دور خودت بچرخ"),
    ("fa", "امروز خیلی خسته‌ام"),
    ("fa", "یه کم برو جلو بعد سرتو بالا کن"),
]


def main() -> None:
    v = Voice()
    if not v.key:
        print("no OpenAI key in this environment (run inside the hub container)")
        return
    compare = "--compare" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    prompts = [(l, t) for l, t in PROMPTS if not only or any(o in t for o in only)]
    ms_new, ms_old = [], []
    for lang, text in prompts:
        v.last_lang = lang
        t0 = time.monotonic()
        d = v.think(text)
        ms = int((time.monotonic() - t0) * 1000)
        ms_new.append(ms)
        if d is None:
            print(f"[{lang}] {text}\n   FAILED ms={ms}\n")
            continue
        plan = brain.build_plan(d["actions"], d["expression"], d["expression_end"], False) if d["actions"] else None
        print(f"[{lang}] {text}\n   ms={ms} searched={v.last_searched} expr={d['expression'] or 'neutral'}"
              f"->{d['expression_end'] or '-'} speak={d['speak']} intent={d['intent'] or '-'}\n"
              f"   plan={(plan or {}).get('plan') or '-'} est={(plan or {}).get('est_s', 0)}s "
              f"travel={(plan or {}).get('travel_mm', 0)}mm notes={(plan or {}).get('notes') or '-'}\n"
              f"   reply={d['reply']!r}\n", flush=True)
        if compare:
            t1 = time.monotonic()
            r, _ = router.classify(v.api, text)
            t2 = time.monotonic()
            chat = ""
            if not (r and r.get("type") == "command"):
                chat, _ = v._chat_web(text)
            t3 = time.monotonic()
            ms_old.append(int((t3 - t1) * 1000))
            print(f"   old: router {int((t2 - t1) * 1000)}ms ({(r or {}).get('intent') or 'chat'}) + chat "
                  f"{int((t3 - t2) * 1000) if not (r and r.get('type') == 'command') else 0}ms\n", flush=True)
    print(f"brain ms: median {statistics.median(ms_new):.0f} mean {statistics.mean(ms_new):.0f} max {max(ms_new)}")
    if ms_old:
        print(f"old router+chat ms: median {statistics.median(ms_old):.0f} mean {statistics.mean(ms_old):.0f} max {max(ms_old)}")


if __name__ == "__main__":
    main()
