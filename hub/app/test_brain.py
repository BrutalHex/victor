#!/usr/bin/env python3
"""One-call brain (reply + expression + motion plan): schema, clamping, caps,
expression mapping, cancellation, and voice turns with a mocked OpenAI reply.
No network: Voice._post is replaced."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("HUB_FACE_DB", os.path.join(tempfile.gettempdir(), "victor-faces-test.db"))
os.environ.setdefault("HUB_FACE_LOCAL", "0")
os.environ.setdefault("HUB_NVIDIA_BUDGET_FILE", os.path.join(tempfile.mkdtemp(), "nv.json"))
os.environ.setdefault("HUB_GREET_FILE", os.path.join(tempfile.mkdtemp(), "greet.json"))

import brain  # noqa: E402

CLIPS = os.path.join(os.path.dirname(__file__), "..", "..", "robot", "agent", "internal", "face", "ddl", "face_clips.json.gz")


def act(do, **kw):
    a = {"do": do, "mm": None, "deg": None, "ms": None, "pos": None, "name": None, "level": None}
    a.update(kw)
    return a


def square(mm=200):
    return [act("drive", mm=mm), act("turn", deg=90)] * 4


class Schema(unittest.TestCase):
    def test_strict_schema_requires_every_property(self):
        def walk(s):
            if s.get("type") == "object" or s.get("type") == ["object"]:
                self.assertFalse(s.get("additionalProperties"))
                self.assertEqual(sorted(s["required"]), sorted(s["properties"]))
                for v in s["properties"].values():
                    walk(v)
            if "items" in s:
                walk(s["items"])
        self.assertTrue(brain.SCHEMA["strict"])
        walk(brain.SCHEMA["schema"])

    def test_ssh_and_never_exposed(self):
        enum = brain.SCHEMA["schema"]["properties"]["intent"]["enum"]
        self.assertFalse([e for e in enum if e and "ssh" in e])
        self.assertNotIn("ssh", " ".join(brain.DO))
        self.assertNotIn("ssh", brain.instructions("x").lower().replace("ssh_", ""))
        for t in ("turn on SSH please", "Schalte S S H aus", "اس اس اچ رو روشن کن", "is ssh on?"):
            self.assertTrue(brain.ssh_text(t), t)
        self.assertFalse(brain.ssh_text("drive in a square"))

    def test_payload_one_call_with_web_search_and_format(self):
        p = brain.payload("gpt-4o-mini", "BASE", [{"role": "user", "content": "hi"}], "drive", {"type": "web_search"})
        self.assertEqual(p["tools"], [{"type": "web_search"}])
        self.assertEqual(p["text"]["format"]["type"], "json_schema")
        self.assertTrue(p["instructions"].startswith("BASE"))
        self.assertEqual(p["input"][-1], {"role": "user", "content": "drive"})
        self.assertNotIn("{INTENTS}", p["instructions"])
        self.assertNotIn("tools", brain.payload("m", "B", [], "x", None))

    def test_prompt_names_every_expression_and_tool(self):
        ins = brain.instructions("")
        for n in brain.NAMES:
            self.assertIn(n, ins)
        for n in ("drive", "turn", "head", "lift", "wait", "trick", "explore", "volume"):
            self.assertIn(n, ins)
        self.assertIn("not always happy", ins)


class Expressions(unittest.TestCase):
    def test_catalog(self):
        need = {"happy", "sad", "angry", "surprised", "confused", "scared", "love", "sleepy", "excited", "curious",
                "bored", "proud", "shy", "disgusted", "thinking", "neutral"}
        self.assertLessEqual(need, set(brain.NAMES))
        self.assertEqual(brain.expression("tired"), "sleepy")
        self.assertEqual(brain.expression("Embarrassed"), "shy")
        self.assertEqual(brain.expression("neutral"), "")
        self.assertEqual(brain.expression("rm -rf"), "")
        self.assertEqual(brain.clip("happy"), "expr_happy")

    @unittest.skipUnless(os.path.exists(CLIPS), "agent clip pack not in this tree (container)")
    def test_every_clip_is_in_the_robot_pack(self):
        pack = json.loads(gzip.open(CLIPS).read())
        for n, (c, _, _) in brain.EXPRESSIONS.items():
            if c:
                self.assertIn(c, pack, n)
                kf = pack[c]["kf"]
                self.assertEqual(kf[0][1:], pack["expr_happy"]["kf"][0][1:]) if c.startswith("expr_") and pack[c]["src"].startswith("anim_eyeposes") else None
                self.assertLessEqual(pack[c]["len"], 5000, n)

    def test_gestures_head_only(self):
        for n in brain.NAMES:
            g = brain.gesture_plan(n)
            if g:
                p = brain.build_plan([], "", "", False)
                self.assertTrue(g.startswith("plan:head "), g)
        self.assertEqual(brain.gesture_plan("sad"), "plan:head droop")


class Plans(unittest.TestCase):
    def test_square(self):
        p = brain.build_plan(square(), "determined", "happy", False)
        self.assertEqual(p["plan"], "plan:face expr_determined;" + ";".join(["drive 200", "turn 90"] * 4) + ";face expr_happy")
        self.assertEqual(p["notes"], [])
        self.assertTrue(p["wheels"])
        self.assertLess(p["est_s"], 30)

    def test_clamps(self):
        p = brain.build_plan([act("drive", mm=9000), act("turn", deg=-5000), act("wait", ms=60000),
                              act("volume", level=11)], "", "", False)
        self.assertEqual(p["steps"], ["drive 500", "turn -720", "wait 5000"])
        self.assertEqual(p["hub"], [("volume", 5)])

    def test_bad_values_dropped(self):
        p = brain.build_plan([act("drive", mm="far"), act("drive", mm=float("nan")), act("turn", deg=None),
                              act("head", pos="sideways"), act("trick", name="leave_charger"),
                              act("trick", name="forward_test"), act("expression", name="evil"), act("fly"),
                              "junk", act("head", pos="up")], "", "", False)
        self.assertEqual(p["steps"], ["head up"])
        self.assertGreaterEqual(len(p["notes"]), 7)

    def test_step_cap(self):
        p = brain.build_plan([act("head", pos="nod")] * 20, "", "", False)
        self.assertEqual(len(p["steps"]), brain.MAX_STEPS)
        self.assertTrue(any("12 steps" in n for n in p["notes"]))

    def test_travel_cap(self):
        p = brain.build_plan([act("drive", mm=500)] * 6, "", "", False)
        self.assertEqual(len(p["steps"]), 4)
        self.assertLessEqual(p["travel_mm"], brain.MAX_TRAVEL_MM)
        self.assertTrue(any("travel" in n for n in p["notes"]))

    def test_time_cap(self):
        p = brain.build_plan([act("wait", ms=5000)] * 8, "", "", False)
        self.assertEqual(len(p["steps"]), 6)
        self.assertTrue(any("longer" in n for n in p["notes"]))

    def test_expression_steps_are_free(self):
        acts = [act("expression", name="happy")] + [act("drive", mm=100), act("turn", deg=60)] * 6
        p = brain.build_plan(acts, "excited", "proud", False)
        self.assertEqual(sum(1 for s in p["steps"] if not s.startswith("face")), 12)
        self.assertEqual(p["steps"][-1], "face expr_proud")

    def test_charger_holds_wheels_and_lift(self):
        for on in (True, None):
            p = brain.build_plan(square() + [act("lift", pos="up"), act("head", pos="up"), act("trick", name="dance")],
                                 "", "", on)
            self.assertEqual(p["steps"], ["head up"])
            self.assertTrue(p["held"])
            self.assertFalse(p["wheels"])

    def test_spin_twice_and_wiggle(self):
        self.assertEqual(brain.build_plan([act("turn", deg=720)], "", "", False)["steps"], ["turn 720"])
        p = brain.build_plan([act("turn", deg=20), act("turn", deg=-40), act("turn", deg=20)], "happy", "", False)
        self.assertEqual(p["steps"], ["face expr_happy", "turn 20", "turn -40", "turn 20"])

    def test_face_only_is_no_plan(self):
        p = brain.build_plan([act("expression", name="sad")], "", "", False)
        self.assertEqual(p["plan"], "")

    def test_hub_actions(self):
        p = brain.build_plan([act("explore"), act("stop"), act("explore_stop")], "", "", False)
        self.assertEqual([k for k, _ in p["hub"]], ["explore", "stop", "explore_stop"])
        self.assertEqual(p["plan"], "")


class CompactSteps(unittest.TestCase):
    def test_strings(self):
        p = brain.build_plan(["drive 200", "turn -90°", "head nod", "lift up", "wait 700 ms", "expression shy",
                              "trick dance", "volume 3", "stop", "drive far", "teleport 3"], "", "", False)
        self.assertEqual(p["steps"], ["drive 200", "turn -90", "head nod", "lift up", "wait 700", "face expr_shy",
                                      "trick dance"])
        self.assertEqual(p["hub"], [("volume", 3), ("stop", None)])
        self.assertEqual(len(p["notes"]), 2)

    def test_circle_macro(self):
        p = brain.build_plan(["circle 300"], "", "", False)
        self.assertEqual(p["steps"], ["drive 150", "turn 60"] * 6)
        self.assertEqual(brain.build_plan(["circle 9000 right"], "", "", False)["steps"][:2], ["drive 250", "turn -60"])

    def test_schema_steps_are_strings(self):
        self.assertEqual(brain.SCHEMA["schema"]["properties"]["actions"]["items"]["type"], "string")


class Parse(unittest.TestCase):
    def test_parse(self):
        d = brain.parse(json.dumps({"reply": "Hi", "expression": "tired", "expression_end": None, "speak": "x",
                                    "intent": "intent_system_ssh_enable", "args": {}, "actions": "no"}))
        self.assertEqual(d["expression"], "sleepy")
        self.assertEqual(d["speak"], "before")
        self.assertIsNone(d["intent"])
        self.assertEqual(d["actions"], [])
        self.assertIsNone(brain.parse("not json"))
        self.assertEqual(brain.parse('noise {"reply": "ok", "actions": []} tail')["reply"], "ok")

    def test_scheduler_cancel(self):
        s, hits = brain.Scheduler(), []
        s.later(0.05, lambda: hits.append(1))
        s.cancel()
        s.later(0.01, lambda: hits.append(2))
        time.sleep(0.15)
        self.assertEqual(hits, [2])


def responses_body(d: dict, searched=False) -> dict:
    out = [{"type": "web_search_call"}] if searched else []
    out.append({"type": "message", "content": [{"type": "output_text", "text": json.dumps(d, ensure_ascii=False)}]})
    return {"output": out}


def decision(reply="Okay!", expression="happy", end=None, speak="before", intent=None, actions=None, args=None):
    return {"reply": reply, "expression": expression, "expression_end": end, "speak": speak, "intent": intent,
            "args": args or {"duration": None, "level": None, "name": None}, "actions": actions or []}


class Turns(unittest.TestCase):
    """Voice turns end to end with a mocked Responses API."""

    def setUp(self):
        import main
        import router
        from session import Session
        self.m, self.router = main, router
        main.pop_cmds()
        self.saved = (brain.ON, main.SESSION, main.VOICE.key, main.VOICE.transcribe, main.VOICE.tts, main.VOICE._post,
                      main.VOICE.chat, router.route, main.VOLUME.path, main.VOLUME.level, main.SCHED)
        brain.ON = True
        main.SESSION = Session(enabled=False, idle_s=0)
        main.VOICE.key = "k"
        main.VOICE.tts = lambda r: b"\x00\x10" * 1600  # 0.1 s
        main.VOLUME.path = os.path.join(tempfile.mkdtemp(), "volume.txt")
        main.VOLUME.level = 4
        main.SCHED = brain.Scheduler()
        main.PLAN_RUN["until"] = 0.0
        self.posts, self.routes, self.chats = [], [], []
        self.reply = decision()
        main.VOICE._post = lambda url, payload, timeout: self.posts.append(payload) or responses_body(self.reply)
        main.VOICE.chat = lambda t: self.chats.append(t) or "chat reply"
        router.route = lambda api, text, lang, key: self.routes.append(text) or (None, "llm")
        self.ground(False)

    def tearDown(self):
        m = self.m
        (brain.ON, m.SESSION, m.VOICE.key, m.VOICE.transcribe, m.VOICE.tts, m.VOICE._post, m.VOICE.chat,
         self.router.route, m.VOLUME.path, m.VOLUME.level, m.SCHED) = self.saved
        m.SCHED.cancel()
        with m.LOCK:
            m.STATE["last_sensor"] = None
        m.pop_cmds()

    def ground(self, on_charger):
        with self.m.LOCK:
            self.m.STATE["last_sensor"] = {"on_charger": on_charger, "explore_enabled": True}

    def turn(self, q, wait=0.0):
        self.m.VOICE.transcribe = lambda pcm: q
        self.m.VOICE.last_lang = "en"
        self.m.run_turn(b"\x00" * 100)
        if wait:
            time.sleep(wait)
        return self.m.pop_cmds()

    def acts(self, cmds):
        return [p.decode() for k, p in cmds if k == self.m.CMD_ACTION]

    def faces(self, cmds):
        return [p.decode() for k, p in cmds if k == self.m.CMD_FACEUI]

    def test_square_one_call_plan_after_speech(self):
        self.reply = decision("One square coming up!", "determined", "happy", "before",
                              actions=["drive 200", "turn 90"] * 4)
        cmds = self.turn("drive in a square")
        self.assertEqual(len(self.posts), 1)  # one model call, no router call
        self.assertEqual(self.routes, [])
        self.assertIn("anim|expr_determined", self.faces(cmds))
        self.assertIn(self.m.CMD_SPEAK, [k for k, _ in cmds])
        self.assertEqual(self.acts(cmds), [])  # waits for the speech (0.1 s)
        later = (time.sleep(0.6), self.m.pop_cmds())[1]
        self.assertEqual(len(self.acts(later)), 1)
        self.assertTrue(self.acts(later)[0].startswith("plan:face expr_determined;drive 200;turn 90"))
        self.assertEqual(self.m.STATE["last_reply"], "One square coming up!")
        self.assertEqual(self.posts[0]["tools"][0]["type"], "web_search")

    def test_stop_cancels_a_waiting_plan(self):
        self.reply = decision("Here I go", "excited", None, "before", actions=[act("turn", deg=720)])
        self.m.VOICE.tts = lambda r: b"\x00\x10" * 8000  # 0.5 s of speech
        self.turn("spin around twice")
        cmds = self.turn("stop")  # fast path, no model call
        self.assertEqual(len(self.posts), 1)
        self.assertIn("stop", self.acts(cmds))
        time.sleep(0.9)
        self.assertFalse([a for a in self.acts(self.m.pop_cmds()) if a.startswith("plan:")])

    def test_stop_words_three_languages_stay_local(self):
        for w in ("Stop", "Halt", "Stopp", "ایست"):
            cmds = self.turn(w)
            self.assertIn("stop", self.acts(cmds), w)
        self.assertEqual(self.posts, [])

    def test_during_sends_plan_with_speech(self):
        self.reply = decision("Wiggle!", "happy", None, "during",
                              actions=[act("turn", deg=20), act("turn", deg=-40), act("turn", deg=20)])
        cmds = self.turn("do a happy wiggle")
        self.assertEqual(self.acts(cmds), ["plan:face expr_happy;turn 20;turn -40;turn 20"])

    def test_long_drive_never_during(self):
        self.reply = decision("Go!", "excited", None, "during", actions=[act("drive", mm=400)])
        cmds = self.turn("drive far forward")
        self.assertEqual(self.acts(cmds), [])  # moved to after the speech
        time.sleep(0.6)
        self.assertEqual(self.acts(self.m.pop_cmds()), ["plan:face expr_excited;drive 400"])

    def test_after_speaks_when_done(self):
        self.reply = decision("Done!", "proud", None, "after", actions=[act("head", pos="up")])
        cmds = self.turn("raise your head and then tell me you are done")
        kinds = [k for k, _ in cmds]
        self.assertNotIn(self.m.CMD_SPEAK, kinds)
        self.assertEqual(self.acts(cmds), ["plan:face expr_proud;head up"])
        time.sleep(1.3)
        self.assertIn(self.m.CMD_SPEAK, [k for k, _ in self.m.pop_cmds()])

    def test_chat_no_actions_expression_and_gesture(self):
        self.reply = decision("Oh no, I'm so sorry.", "sad")
        cmds = self.turn("my cat died yesterday")
        self.assertIn("anim|expr_sad", self.faces(cmds))
        self.assertEqual(self.acts(cmds), ["plan:head droop"])
        self.assertEqual(self.m.STATE["last_brain"]["expression"], "sad")

    def test_neutral_chat_sends_nothing_physical(self):
        self.reply = decision("It's 21 degrees.", "neutral")
        cmds = self.turn("what's the weather")
        self.assertEqual(self.acts(cmds), [])
        self.assertIn("idle|", self.faces(cmds))

    def test_on_charger_says_so(self):
        self.ground(True)
        self.reply = decision("Okay!", "happy", None, "before", actions=square())
        cmds = self.turn("drive in a square")
        time.sleep(0.4)
        cmds += self.m.pop_cmds()
        self.assertFalse([a for a in self.acts(cmds) if "drive" in a or "turn" in a])
        self.assertIn("charger", self.m.STATE["last_reply"].lower())

    def test_stock_intent_via_model(self):
        self.reply = decision("Sure", "happy", intent="intent_clock_settimer_extend",
                              args={"duration": "5 minutes", "level": None, "name": None})
        self.turn("could you set a timer for five minutes for my tea")
        self.assertGreater(self.m.TIMER.left(), 200)
        self.m.TIMER.cancel()
        self.assertEqual(self.m.STATE["last_intent"]["intent"], "intent_clock_settimer_extend")

    def test_ssh_text_never_reaches_the_model(self):
        self.turn("Vector, could you please turn SSH on for me")
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.routes), 1)

    def test_model_failure_falls_back(self):
        def boom(url, payload, timeout):
            raise TimeoutError("slow")
        self.m.VOICE._post = boom
        self.turn("tell me something nice about penguins")
        self.assertEqual(self.routes, ["tell me something nice about penguins"])
        self.assertEqual(self.chats, ["tell me something nice about penguins"])

    def test_volume_action(self):
        self.reply = decision("Louder!", "happy", actions=[act("volume", level=2)])
        self.turn("please make your voice level two and dance")
        self.assertEqual(self.m.VOLUME.level, 2)


class SessionRestore(unittest.TestCase):
    def test_restart_keeps_an_awake_session(self):
        import main
        from session import Session
        saved = (main.SESSION, main.SESSION_FILE)
        try:
            main.SESSION_FILE = os.path.join(tempfile.mkdtemp(), "session.json")
            main.SESSION = Session(enabled=True, idle_s=0)
            self.assertFalse(main.session_restore())  # no file
            json.dump({"state": "awake", "t": time.time() - 60}, open(main.SESSION_FILE, "w"))
            self.assertTrue(main.session_restore())
            self.assertEqual(main.SESSION.state, "awake")
            main.SESSION = Session(enabled=True, idle_s=0)
            json.dump({"state": "awake", "t": time.time() - 3600}, open(main.SESSION_FILE, "w"))
            self.assertFalse(main.session_restore())  # too old
            json.dump({"state": "asleep", "t": time.time()}, open(main.SESSION_FILE, "w"))
            self.assertFalse(main.session_restore())
        finally:
            main.SESSION, main.SESSION_FILE = saved


class MoveBack(unittest.TestCase):
    """Live 10 Oct 15:37: 'move back' did nothing. Fast-path drive commands had
    an empty spoken ack (silent turn), 'Hey guys, so move back.' was dropped by
    the language filter and 'just back.' was called unclear."""

    PHRASES = ["move back", "go back", "back up", "backwards", "please move back", "Please move back.", "step back",
               "reverse", "fahr zurück", "geh zurück", "rückwärts", "برو عقب", "عقب برو", "برگرد عقب"]

    def setUp(self):
        import main
        from session import Session
        self.m = main
        main.pop_cmds()
        self.saved = (main.SESSION, main.VOICE.key, main.VOICE.transcribe, main.VOICE.tts, main.VOICE.chat, brain.ON)
        main.SESSION = Session(enabled=False, idle_s=0)
        main.VOICE.key = "k"
        main.VOICE.tts = lambda r: b"\x00\x10" * 800
        main.VOICE.chat = lambda t: "chat"
        brain.ON = True

    def tearDown(self):
        m = self.m
        m.SESSION, m.VOICE.key, m.VOICE.transcribe, m.VOICE.tts, m.VOICE.chat, brain.ON = self.saved
        with m.LOCK:
            m.STATE["last_sensor"] = None
        m.pop_cmds()

    def turn(self, q, on_charger=False):
        with self.m.LOCK:
            self.m.STATE["last_sensor"] = {"on_charger": on_charger}
        self.m.VOICE.transcribe = lambda pcm: q
        self.m.VOICE.last_lang = "fa" if any("\u0600" <= c <= "\u06ff" for c in q) else ("de" if "ü" in q else "en")
        self.m.run_turn(b"\x00" * 100)
        return self.m.pop_cmds()

    def test_all_phrases_back_up_with_an_ack(self):
        for q in self.PHRASES:
            cmds = self.turn(q)
            self.assertIn((self.m.CMD_ACTION, b"backup"), cmds, q)
            self.assertIn(self.m.CMD_SPEAK, [k for k, _ in cmds], q)
            self.assertTrue(self.m.STATE["last_reply"], q)

    def test_on_charger_says_why(self):
        cmds = self.turn("move back", on_charger=True)
        self.assertNotIn(self.m.CMD_ACTION, [k for k, _ in cmds])
        self.assertIn("charger", self.m.STATE["last_reply"].lower())

    def test_other_drive_commands_are_not_silent(self):
        for q in ("turn left", "turn right", "turn around", "go forward"):
            self.turn(q)
            self.assertTrue(self.m.STATE["last_reply"], q)

    def test_noisy_transcripts_survive_the_filters(self):
        import lang
        import wake
        self.assertEqual(lang.classify("Hey guys, so move back.")[0], "en")
        self.assertFalse(wake.unclear("just back."))
        self.assertFalse(wake.unclear("step back"))
        self.assertEqual(lang.classify("Fahr bitte ein Stück zurück")[0], "de")

    def test_long_persian_moves_reach_the_brain(self):
        import router
        for q in ("به سمت راست حرکت کن", "سیصد و شصت درجه بچرخ", "Hey guys, so move back."):
            self.assertIsNone(router.fast(q))  # > 4 words: the model plans it (turn -90 / turn 360 / drive -120)


class ActionResult(unittest.TestCase):
    def setUp(self):
        import main
        self.m = main
        self.said = []
        self.saved = main.speak_turn
        main.speak_turn = lambda text, why, show=b"": self.said.append(text) or 1
        main.ACTION.update(seq=None, sent=0.0, lang="en")

    def tearDown(self):
        self.m.speak_turn = self.saved

    def ev(self, seq, res):
        self.m._action_events({"action_seq": seq, "action_result": res})
        time.sleep(0.05)

    def test_refusal_is_spoken(self):
        m = self.m
        self.ev(4, 0)  # first packet: baseline only
        m.VOICE.last_lang = "de"
        m.queue_cmd(m.CMD_ACTION, b"backup")
        self.ev(5, 3)  # rear cliff
        self.assertEqual(self.said, [__import__("intents").say("why_rear_cliff", "de")])
        self.ev(6, 1)  # ok: silent
        self.ev(7, 12)  # cancelled: silent
        self.assertEqual(len(self.said), 1)
        m.pop_cmds()

    def test_old_results_not_spoken(self):
        self.ev(1, 0)
        self.ev(2, 7)  # no recent voice action
        self.assertEqual(self.said, [])

    def test_sensor_unpack(self):
        import struct
        import protocol
        p = bytes(39) + struct.pack("<4H", 3900, 0, 0, 0) + struct.pack("<HHB", 2, 9, 3)
        d = protocol.unpack_sensor(p)
        self.assertEqual((d["button_presses"], d["action_seq"], d["action_result"]), (2, 9, 3))
        self.assertIsNone(protocol.unpack_sensor(p[:49])["action_seq"])


if __name__ == "__main__":
    unittest.main()
