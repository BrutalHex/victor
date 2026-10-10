"""Live check of the command/chat router on mixed EN/DE/FA lines (uses the OpenAI key in the hub).
Run in the hub container: python router_eval.py   -> prints each decision, latency, and a score."""

from __future__ import annotations

import statistics
import sys

import router
from voice import Voice

# (text, lang, expected intent or None = chat)
CASES = [
    ("what do you think about dancing", "en", None),
    ("can you dance for me", "en", "intent_imperative_dance"),
    ("turn left", "en", "intent_imperative_turnleft"),
    ("I turned left yesterday", "en", None),
    ("Hey Vector, could you take a picture of me please", "en", "intent_photo_take_extend"),
    ("do you like taking pictures", "en", None),
    ("set a timer for five minutes", "en", "intent_clock_settimer_extend"),
    ("how long is five minutes in seconds", "en", None),
    ("my name is Mohammad", "en", "intent_names_username_extend"),
    ("I was sleeping all day", "en", None),
    ("go to sleep Vector", "en", "intent_system_sleep"),
    ("tell me a story about a robot that explores the world", "en", None),
    ("kannst du für mich tanzen", "de", "intent_imperative_dance"),
    ("Ich habe gestern getanzt", "de", None),
    ("dreh dich nach links", "de", "intent_imperative_turnleft"),
    ("Wie spät ist es eigentlich?", "de", "intent_clock_time"),
    ("Was ist die Hauptstadt von Frankreich?", "de", None),
    ("mach mal ein Foto", "de", "intent_photo_take_extend"),
    ("برای من برقص", "fa", "intent_imperative_dance"),
    ("رقص دوست داری؟", "fa", None),
    ("بپیچ به چپ", "fa", "intent_imperative_turnleft"),
    ("دیروز رفتم سمت چپ خیابون", "fa", None),
    ("الان ساعت چنده؟", "fa", "intent_clock_time"),
    ("یه عکس بگیر", "fa", "intent_photo_take_extend"),
    ("pumping up the volume is my favourite song", "en", None),
    ("make it louder", "en", "intent_imperative_volumeup"),
]


def main() -> int:
    v = Voice()
    if not v.key:
        print("no key")
        return 2
    ok, times = 0, []
    for text, lang, want in CASES:
        it, how = router.route(v.api, text, lang)
        got = it.name if it else None
        good = got == want
        ok += good
        print(f"{'OK ' if good else 'BAD'} {how:5s} want={want} got={got} arg={it.arg if it else ''!r} :: {text}", flush=True)
    print(f"score {ok}/{len(CASES)}")
    # latency of the API path alone (fast path skipped)
    for text, lang, _ in CASES[:10]:
        d, ms = router.classify(v.api, text)
        times.append(ms)
    print(f"llm latency ms: median={statistics.median(times):.0f} max={max(times)} all={times}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
