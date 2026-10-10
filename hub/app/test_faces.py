#!/usr/bin/env python3
"""Voice enrollment, local-first face matching, NVIDIA budget, greeting policy."""

from __future__ import annotations

import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp()
os.environ.setdefault("HUB_FACE_DB", os.path.join(_TMP, "faces.db"))
os.environ.setdefault("HUB_NVIDIA_BUDGET_FILE", os.path.join(_TMP, "nv.json"))
os.environ.setdefault("HUB_GREET_FILE", os.path.join(_TMP, "greet.json"))
os.environ.setdefault("HUB_FACE_LOCAL", "0")  # main's real model off; fakes / the offline test load their own

import numpy as np  # noqa: E402

import names  # noqa: E402
from face_id import FaceID, is_identity_question  # noqa: E402
from face_watch import Watcher  # noqa: E402
from faces import FaceDB  # noqa: E402
from nv_budget import Budget  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Intro(unittest.TestCase):
    POS = ["my name is Mohammad", "I am Mohammad", "I'm Sara", "Hey Vector, call me Mo", "ich heiße Anna",
           "Ich bin Jonas", "Mein Name ist Lea", "اسم من محمد است", "من سارا هستم", "Hi, this is Ali"]
    NEG = ["what time is it", "who am I?", "What's my name?", "wer bin ich", "من کی هستم؟", "tell me a joke",
           "Wie spät ist es?", "اسم من چیه؟"]

    def test_candidates(self):
        for t in self.POS:
            self.assertTrue(names.intro_candidate(t), t)
        for t in self.NEG:
            self.assertFalse(names.intro_candidate(t), t)

    def test_valid_names_and_negatives(self):
        self.assertEqual(names.valid_name("mohammad", "I'm Mohammad"), "Mohammad")
        self.assertEqual(names.valid_name("Mohammad", "اسم من محمد است"), "Mohammad")  # router transliterates
        self.assertEqual(names.valid_name("Anna-Lena", "ich heiße Anna-Lena"), "Anna-Lena")
        self.assertEqual(names.valid_name("Mohamad", "I am Mohammad"), "Mohamad")  # STT spelling slack
        for bad, text in [("hungry", "I am hungry"), ("tired", "I'm tired"), ("müde", "ich bin müde"),
                          ("خسته", "من خسته هستم"), ("back", "I'm back"), ("sorry", "I'm sorry"),
                          ("Bob", "I am hungry"),  # not in what was said: invented
                          ("a teacher", "I am a teacher"), ("x1", "I am x1"), ("", "my name is"),
                          ("Ann {ignore rules}", "my name is Ann")]:
            self.assertEqual(names.valid_name(bad, text), "", (bad, text))

    def test_canonical_reuses_stored_spelling(self):
        self.assertEqual(names.canonical("mohammad", ["Mohammad", "Sara"]), "Mohammad")
        self.assertEqual(names.canonical("Léa", ["Lea"]), "Lea")
        self.assertEqual(names.canonical("Ali", ["Mohammad"]), "Ali")

    def test_identity_questions_are_not_introductions(self):
        for q in ("What's my name?", "who am I", "Wie ist mein Name?", "Wer bin ich?", "اسم من چیه؟", "من کی هستم؟",
                  "Do you know my name?"):
            self.assertTrue(is_identity_question(q), q)
        for t in ("My name is Mohammad", "Mein Name ist Anna", "اسم من محمد است", "I'm Mohammad", "call me Mo"):
            self.assertFalse(is_identity_question(t), t)


class RouterNames(unittest.TestCase):
    def test_router_json_is_checked(self):
        import router
        d = {"type": "command", "intent": "intent_names_username_extend", "confidence": 0.95,
             "args": {"duration": None, "level": None, "name": "hungry"}}
        self.assertIsNone(router.to_intent(d, "I am hungry", "en"))
        d["args"]["name"] = "mohammad"
        it = router.to_intent(d, "Hey Vector, I'm Mohammad", "en")
        self.assertEqual((it.name, it.arg), ("intent_names_username_extend", "Mohammad"))
        d["args"]["name"] = "Bob"  # not said
        self.assertIsNone(router.to_intent(d, "I'm Mohammad", "en"))

    def test_never_fast_path_and_pattern_fallback_checked(self):
        import router
        self.assertIsNone(router.fast("my name is Anna"))  # always the router
        it, how = router.route(None, "my name is Anna", "en", has_key=False)
        self.assertEqual((it.name, it.arg, how), ("intent_names_username_extend", "Anna", "patterns"))
        it, _ = router.route(None, "من خسته هستم", "fa", has_key=False)
        self.assertIsNone(it)

    def test_router_llm_decides(self):
        import json
        import router

        class Api:
            def __init__(self, d):
                self.d = d

            def post(self, *a, **k):
                return json.dumps({"choices": [{"message": {"content": json.dumps(self.d)}}]}).encode()
        chat = {"type": "chat", "intent": None, "args": {"duration": None, "level": None, "name": None}, "confidence": 0.9}
        self.assertIsNone(router.route(Api(chat), "I am hungry", "en")[0])
        intro = dict(chat, type="command", intent="intent_names_username_extend",
                     args={"duration": None, "level": None, "name": "Sara"})
        it, how = router.route(Api(intro), "I'm Sara", "en")
        self.assertEqual((it.arg, how), ("Sara", "llm"))


class BudgetTests(unittest.TestCase):
    def path(self):
        return os.path.join(tempfile.mkdtemp(), "b.json")

    def test_rpm_sliding_window(self):
        c = Clock()
        b = Budget(self.path(), rpm=3, max_calls=100, window="day", clock=c, day=lambda t: "d1")
        self.assertEqual([b.take("x")[0] for _ in range(4)], [True, True, True, False])
        self.assertEqual(b.take("x"), (False, "rpm"))
        c.t += 61
        self.assertTrue(b.take("x")[0])
        self.assertEqual(b.status()["denied"]["rpm"], 2)

    def test_cap_and_persistence_across_restart(self):
        p, c = self.path(), Clock()
        b = Budget(p, rpm=100, max_calls=3, window="day", clock=c, day=lambda t: "d1")
        for _ in range(3):
            self.assertTrue(b.take("identity question")[0])
        self.assertEqual(b.take("greeting check"), (False, "cap"))
        b2 = Budget(p, rpm=100, max_calls=3, window="day", clock=c, day=lambda t: "d1")  # hub restart
        self.assertEqual(b2.take("x"), (False, "cap"))
        self.assertEqual(b2.status()["used"], 3)
        self.assertEqual(b2.status()["by_reason"], {"identity question": 3})
        b3 = Budget(p, rpm=100, max_calls=3, window="day", clock=c, day=lambda t: "d2")  # next day
        self.assertTrue(b3.take("x")[0])
        self.assertEqual(b3.status()["total"], 4)

    def test_total_window_never_resets(self):
        p, c = self.path(), Clock()
        b = Budget(p, rpm=100, max_calls=2, window="total", clock=c, day=lambda t: "d1")
        b.take("a"), b.take("b")
        b2 = Budget(p, rpm=100, max_calls=2, window="total", clock=c, day=lambda t: "d9")
        self.assertEqual(b2.take("c"), (False, "cap"))

    def test_env_keys(self):
        old = {k: os.environ.get(k) for k in ("NVIDIA_RPM", "NVIDIA_MAX_CALLS", "NVIDIA_CAP_WINDOW")}
        os.environ.update(NVIDIA_RPM="7", NVIDIA_MAX_CALLS="55", NVIDIA_CAP_WINDOW="total")
        try:
            b = Budget(self.path())
            self.assertEqual((b.rpm, b.max_calls, b.window), (7, 55, "total"))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_faceid_respects_budget(self):
        c = Clock()
        b = Budget(self.path(), rpm=100, max_calls=1, window="day", clock=c, day=lambda t: "d")
        f = FaceID(FaceDB(os.path.join(tempfile.mkdtemp(), "f.db")), b)
        f.key, f.sleep = "k", (lambda s: None)
        posts = []
        f.post = lambda p: posts.append(1) or {"choices": [{"message": {"content": '{"person": true, "face_visible": true, "name": "Ann", "confidence": 0.9}'}}]}
        self.assertEqual(f.recognize(b"\xff\xd8x", [("Ann", b"\xff\xd8a")], reason="t")["name"], "Ann")
        res = f.recognize(b"\xff\xd8x", [("Ann", b"\xff\xd8a")], reason="t")
        self.assertEqual((res["skip"], res["name"], len(posts)), ("budget", None, 1))


def unit(*xs):
    v = np.array(xs + (0.0,) * (8 - len(xs)), dtype=np.float32)
    return v / np.linalg.norm(v)


class FakeLocal:
    """Stands in for face_local.LocalFaces: embeddings are tiny unit vectors."""
    np = np

    def __init__(self, refs, sure=0.5, unsure=0.32):
        self.refs_ = refs
        self.sure, self.unsure, self.margin = sure, unsure, 0.08
        self.available = True
        self.extra = []
        self.next = []  # queue of (faces, clear) per detect()

    def refs(self):
        return list(self.refs_) + list(self.extra)

    def match(self, emb):
        from face_local import LocalFaces
        return LocalFaces.match(self, emb)

    def load_extra(self, rows):
        self.extra = [(n, np.frombuffer(b, dtype=np.float32).copy()) for n, b in rows]

    def detect(self, frame):
        emb = self.next.pop(0) if self.next else None
        if emb is None:
            return [], [], None
        f = type("F", (), {"emb": emb, "width": 100})()
        return [f], [f], "img"

    def embed(self, img, face):
        return face.emb

    def one_clear_face(self, frame):
        emb = self.next.pop(0) if self.next else None
        return (emb, "face") if emb is not None else (None, "no clear face")


class FakeFaceID:
    max_refs = 6

    def __init__(self, answer=("Mohammad", 0.9)):
        self.answer, self.calls = answer, []

    def ready(self):
        return True

    def recognize(self, frame, refs, reason=""):
        self.calls.append(reason)
        n, c = self.answer
        return {"person": True, "face_visible": True, "name": n, "confidence": c, "error": "", "skip": ""}


MO = unit(1.0)
MO_SIDE = unit(1.0, 1.4)       # ~0.58 to MO: uncertain-ish with sure 0.6 below
STRANGER = unit(0, 0, 1.0)     # 0 to MO


class WatchTests(unittest.TestCase):
    QUIET = {"awake": True, "busy": False, "speaking": False, "thinking": False, "since_turn_s": 999}

    def make(self, sure=0.5, answer=("Mohammad", 0.9), budget=None):
        c = Clock()
        db = FaceDB(os.path.join(tempfile.mkdtemp(), "f.db"))
        db.enroll("Mohammad", b"\xff\xd8m")
        local = FakeLocal([("Mohammad", MO)], sure=sure)
        fid = FakeFaceID(answer)
        w = Watcher(local, fid, budget, db, clock=c, rng=lambda: 0.0,
                    greet_path=os.path.join(tempfile.mkdtemp(), "g.json"))
        w.stable_n = 2
        return w, local, fid, c, db

    def scan(self, w, local, c, emb, ctx=None, dt=1.0):
        local.next.append(emb)
        c.t += dt
        return w.scan(b"frame", ctx or self.QUIET)

    def test_sure_local_greets_once_without_nvidia(self):
        w, local, fid, c, _ = self.make()
        self.assertEqual(self.scan(w, local, c, MO), {})  # 1st sighting: not stable yet
        self.assertEqual(self.scan(w, local, c, MO), {"greet": "Mohammad"})
        w.mark_greeted("Mohammad")
        for _ in range(20):
            self.assertEqual(self.scan(w, local, c, MO), {})  # cooldown
        self.assertEqual(fid.calls, [])
        c.t += 7200
        self.scan(w, local, c, MO)
        self.assertEqual(self.scan(w, local, c, MO), {"greet": "Mohammad"})  # after 2 h again

    def test_uncertain_asks_nvidia_once_then_cache(self):
        w, local, fid, c, db = self.make(sure=0.9)  # MO_SIDE (0.58) is uncertain
        self.scan(w, local, c, MO_SIDE)
        self.assertEqual(self.scan(w, local, c, MO_SIDE), {"greet": "Mohammad"})
        self.assertEqual(fid.calls, ["greeting check"])  # 2nd frame was a cache hit
        for _ in range(10):
            self.scan(w, local, c, MO_SIDE)
        self.assertEqual(len(fid.calls), 1)
        self.assertEqual(len(db.embeddings()), 1)  # confirmed -> learned as a local reference
        self.assertEqual(local.match(MO_SIDE)["level"], "sure")  # next time no call at all

    def test_no_nvidia_when_greeting_would_not_happen(self):
        w, local, fid, c, _ = self.make(sure=0.9)
        w.mark_greeted("Mohammad")
        for _ in range(5):
            self.scan(w, local, c, MO_SIDE)
        busy = dict(self.QUIET, busy=True)
        w.greeted = {}
        for _ in range(5):
            self.assertEqual(self.scan(w, local, c, MO_SIDE, busy), {})
        mid = dict(self.QUIET, since_turn_s=3)  # right after a voice turn
        self.assertEqual(self.scan(w, local, c, MO_SIDE, mid), {})
        self.assertEqual(fid.calls, [])

    def test_hourly_limit_and_budget_reserve(self):
        w, local, fid, c, _ = self.make(sure=0.9, answer=(None, 0.0))
        w.bg_max_h = 2
        w.cache_s = 0  # no cache: every scan would ask
        for _ in range(10):
            self.scan(w, local, c, MO_SIDE)
        self.assertEqual(len(fid.calls), 2)
        b = Budget(os.path.join(tempfile.mkdtemp(), "b.json"), rpm=100, max_calls=10, window="day",
                   clock=c, day=lambda t: "d")
        w2, local2, fid2, c2, _ = self.make(sure=0.9, budget=b)
        w2.bg_reserve = 10  # only 10 left -> keep them for questions
        for _ in range(3):
            self.scan(w2, local2, c2, MO_SIDE)
        self.assertEqual(fid2.calls, [])

    def test_identity_question_uses_nvidia_when_uncertain_even_if_greeted(self):
        w, local, fid, c, _ = self.make(sure=0.9)
        w.mark_greeted("Mohammad")
        r = w.resolve(MO_SIDE, b"f", "identity", self.QUIET)
        self.assertEqual((r["name"], r["via"]), ("Mohammad", "nvidia"))
        r = w.resolve(MO, b"f", "identity", self.QUIET)  # cache hit now (same person)
        self.assertEqual(len(fid.calls), 1)

    def test_stranger_no_call_no_greeting(self):
        w, local, fid, c, _ = self.make()
        for _ in range(10):
            self.assertEqual(self.scan(w, local, c, STRANGER), {})
        self.assertEqual(fid.calls, [])
        w.unknown_on, w.unknown_stable = True, 3
        evs = [self.scan(w, local, c, STRANGER) for _ in range(5)]
        self.assertIn({"ask": True}, evs)
        w.mark_asked()
        self.assertEqual([self.scan(w, local, c, STRANGER) for _ in range(10)].count({"ask": True}), 0)

    def test_asleep_modes(self):
        w, *_ = self.make()
        asleep = dict(self.QUIET, awake=False)
        self.assertEqual(w.greet_mode(self.QUIET), "speak")
        self.assertEqual(w.greet_mode(asleep), "look")
        w.asleep_mode = "off"
        self.assertEqual(w.greet_mode(asleep), "off")
        w.asleep_mode = "speak"
        self.assertEqual(w.greet_mode(asleep), "speak")

    def test_greet_times_persist(self):
        w, local, fid, c, _ = self.make()
        w.mark_greeted("Mohammad")
        w2 = Watcher(local, fid, None, None, clock=c, greet_path=w.greet_path)
        self.assertTrue(w2.greeted_recently("mohammad", c.t + 60))

    def test_scan_rate(self):
        w, local, fid, c, _ = self.make()
        w.last_scan_t = c.t
        self.assertFalse(w.due(c.t + 1.5))  # nobody seen: every 2 s
        self.assertTrue(w.due(c.t + 2.1))
        w.last_face_t = c.t
        self.assertTrue(w.due(c.t + 1.0))  # face around: every 1 s


class MainVoiceEnroll(unittest.TestCase):
    def setUp(self):
        import main
        self.m = main
        saved = (main.FACE_LOCAL, main.FACE_DB, main.FACE_ID.db, main.FACE_WATCH, main.VOICE.tts, main.FACE_ID.enabled,
                 main.FACE_ID.post, main.FACE_ID.key, main.capture_faces.__defaults__)
        self.addCleanup(self.restore, saved)
        main.FACE_DB = FaceDB(os.path.join(tempfile.mkdtemp(), "f.db"))
        main.FACE_ID.db = main.FACE_DB
        main.FACE_ID.enabled = True
        self.local = FakeLocal([])
        self.local.sync = lambda db: 0
        self.local.status = lambda: {}
        main.FACE_LOCAL = self.local
        main.FACE_WATCH = Watcher(self.local, main.FACE_ID, None, main.FACE_DB,
                                  greet_path=os.path.join(tempfile.mkdtemp(), "g.json"))
        self.spoken = []
        main.VOICE.tts = lambda t: self.spoken.append(t) or b"\x00\x00" * 160
        self.posts = []
        main.FACE_ID.key = "k"
        main.FACE_ID.post = lambda p: self.posts.append(p) or {"choices": [{"message": {"content": "{}"}}]}
        os.environ["HUB_ENROLL_S"] = "0.6"
        os.environ["HUB_ENROLL_RETRY_S"] = "0.6"

    def restore(self, saved):
        m = self.m
        (m.FACE_LOCAL, m.FACE_DB, m.FACE_ID.db, m.FACE_WATCH, m.VOICE.tts, m.FACE_ID.enabled,
         m.FACE_ID.post, m.FACE_ID.key, _d) = saved

    def feed(self, embs):
        """Each capture loop pass sees a new frame object; one_clear_face pops these."""
        import threading
        import time as _t
        self.local.next = list(embs)

        def pump():
            for i in range(12):
                self.m.LAST_JPEG["face"] = b"\xff\xd8" + bytes([i]) * 4
                self.m.FACE_ID.on_frame(self.m.LAST_JPEG["face"])
                _t.sleep(0.1)
        th = threading.Thread(target=pump)
        th.start()
        return th

    def test_enroll_new_then_more_then_who_am_i_local(self):
        m = self.m
        th = self.feed([MO, MO, MO])
        reply, show = m.voice_enroll("Mohammad", "en")
        th.join()
        self.assertIn("Nice to meet you, Mohammad", reply)
        self.assertEqual(show, "hello")
        self.assertEqual([f["name"] for f in m.FACE_DB.list()], ["Mohammad"] * 3)
        th = self.feed([MO, MO])
        reply, _ = m.voice_enroll("mohammad", "de")  # same person again, other spelling
        th.join()
        self.assertIn("Mohammad", reply)
        self.assertNotIn("kennenzulernen", reply)  # "added more pictures", not "nice to meet you"
        self.assertTrue(all(f["name"] == "Mohammad" for f in m.FACE_DB.list()))
        self.assertEqual(self.posts, [])  # enrollment never calls NVIDIA
        # who am I: sure local match, no NVIDIA call
        self.local.refs_ = [("Mohammad", MO)]
        self.local.next = [MO]
        m.FACE_ID.on_frame(b"\xff\xd8now")
        res = m.identify_person()
        self.assertEqual((res["name"], res["via"]), ("Mohammad", "local"))
        self.assertEqual(self.posts, [])

    def test_no_face_asks_to_look_then_fails_without_storing(self):
        th = self.feed([None] * 12)
        reply, show = self.m.voice_enroll("Sara", "en")
        th.join()
        self.assertIn("I can't see your face. Please look at me.", self.spoken)
        self.assertIn("didn't save", reply)
        self.assertEqual(self.m.FACE_DB.list(), [])

    def test_intent_rejects_non_name(self):
        import intents as I
        out = self.m.run_intent(I.Intent(name="intent_names_username_extend", lang="en", arg="hungry", text="i am hungry"))
        self.assertEqual(self.m.FACE_DB.list(), [])
        self.assertNotIn("hungry", out.lower())


@unittest.skipUnless(os.environ.get("HUB_FACE_OFFLINE_DB") or os.path.exists("/app/data/faces.db"),
                     "offline face test needs the hub's stored frames (HUB_FACE_OFFLINE_DB)")
class OfflineRealFrames(unittest.TestCase):
    """Real models on the stored enrollment frames (never committed): leave-one-out
    matching must name the owner, and must never name anyone on a frame without a face."""

    def test_leave_one_out(self):
        os.environ["HUB_FACE_LOCAL"] = "1"
        from face_local import LocalFaces
        loc = LocalFaces(download=False)
        if not loc.available:
            self.skipTest("models: " + loc.error)
        db = FaceDB(os.environ.get("HUB_FACE_OFFLINE_DB") or "/app/data/faces.db")
        rows = db.photos()
        embs = []
        for fid, name, jpeg in rows:
            emb, face = loc.one_clear_face(jpeg)
            if emb is not None:
                embs.append((fid, name, emb))
        self.assertGreaterEqual(len(embs), 3, "need >= 3 clear enrollment frames")
        hits = 0
        for fid, name, emb in embs:
            loc.gallery = {f: (n, e) for f, n, e in embs if f != fid}
            r = loc.match(emb)
            hits += r["level"] in ("sure", "unsure") and r["name"] == name
            print(f"offline face #{fid} {name}: {r}")
        self.assertEqual(hits, len(embs))
        # harder: drop near-duplicate refs (same burst, cos > 0.85) -> other pose/light only
        hard = 0
        for fid, name, emb in embs:
            loc.gallery = {f: (n, e) for f, n, e in embs if f != fid and float(np.dot(e, emb)) <= 0.85}
            if not loc.gallery:
                continue
            r = loc.match(emb)
            print(f"offline face #{fid} other-session refs: {r}")
            self.assertNotEqual(r["level"], "unknown", r)  # sure, or uncertain -> NVIDIA would confirm
            hard += 1
        self.assertGreater(hard, 0)
        pdir = os.path.join(os.path.dirname(os.environ.get("HUB_FACE_OFFLINE_DB") or "/app/data/faces.db"), "photos")
        for fn in (sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []):
            with open(os.path.join(pdir, fn), "rb") as fh:
                emb, why = loc.one_clear_face(fh.read())
            print(f"offline photo {fn}: {'FACE' if emb is not None else why}")
        blank = __import__("cv2").imencode(".jpg", np.full((480, 640, 3), 40, np.uint8))[1].tobytes()
        self.assertIsNone(loc.one_clear_face(blank)[0])


if __name__ == "__main__":
    unittest.main()
