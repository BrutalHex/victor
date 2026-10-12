"""Real OpenAI, text only: the brain must fire pet_answer for any wording of
'who is my dog/cat?' (M.O 12 Oct: "these should be like commands so the
model understands to fire them"). Run in the hub container:
HUB_LIVE_OPENAI=1 python -m pytest -q test_pet_live.py"""
import json
import os
import unittest
import urllib.request
import re

import brain
import voice

APOLOGY = r"sorry|not a (?:dog|cat)|don't have|information|i'm not|leider|bin kein|متاسف|نیستم|اطلاعات"
LIVE = os.environ.get("HUB_LIVE_OPENAI") == "1" and os.environ.get("OPENAI_API_KEY")

CASES = [  # (text, pet); the last five are paraphrases not in the prompt
    ("Who is my dog?", "dog"), ("Who's my good boy?", "dog"), ("Who is my kitty?", "cat"),
    ("Wer ist mein Hund?", "dog"), ("سگ من کیه؟", "dog"), ("گربه من کیه؟", "cat"),
    ("Who's the best doggo in this house?", "dog"), ("Is that my sweet little puppy?", "dog"),
    ("Wer ist hier meine süße Mieze?", "cat"), ("Hey, who's mommy's little kitten?", "cat"),
    ("Bist du mein braver Hund?", "dog"), ("تو گربه منی؟", "cat"),
]
SOUNDS = [("Can you purr for me?", "purr"), ("Bark!", "dog"), ("Make a bird noise", "bird")]


def think(text: str) -> dict:
    model = os.environ.get("HUB_SEARCH_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    p = brain.payload(model, voice.system_prompt(), [], text, None)
    req = urllib.request.Request("https://api.openai.com/v1/responses", json.dumps(p).encode(),
                                 {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                                  "Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=40))
    raw, _ = voice._responses_text(data)
    d, why = brain.sanitize(brain.parse(raw), text)
    return d


@unittest.skipUnless(LIVE, "set HUB_LIVE_OPENAI=1 (real API calls)")
class LivePet(unittest.TestCase):
    def test_pet_answer(self):
        bad = []
        for text, pet in CASES:
            d = think(text)
            plan = brain.build_plan(d.get("actions") or [], on_charger=True)
            hub = plan["hub"] if plan else []
            print(f"{text!r} -> actions={d.get('actions')} reply={d.get('reply')!r}")
            reply = d.get("reply") or ""
            if ("pet_answer", pet) not in hub or len(reply.split()) > 7 or re.search(APOLOGY, reply, re.I):
                bad.append((text, d.get("actions"), d.get("reply")))
        self.assertLessEqual(len(bad), 1, bad)  # 12 Oct run: 12/12 fired, replies '', 'Me!', 'Ich! Wuff!', 'منم!'...

    def test_other_sounds(self):
        for text, n in SOUNDS:
            d = think(text)
            plan = brain.build_plan(d.get("actions") or [], on_charger=True)
            print(f"{text!r} -> {d.get('actions')} reply={d.get('reply')!r}")
            hub = plan["hub"] if plan else []
            self.assertTrue(("animal_sound", n) in hub or ("pet_answer", n) in hub, (text, hub))
