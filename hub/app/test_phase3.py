#!/usr/bin/env python3
"""Phase 3 hub unit tests (stdlib only)."""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
import zlib

os.environ.setdefault("HUB_FACE_DB", os.path.join(tempfile.gettempdir(), "victor-faces-test.db"))

from explore import BACK_OFF, LOOK_DOWN, LOOK_UP, STOP, Explorer  # noqa: E402
from faces import FaceDB, cosine, embed  # noqa: E402
from protocol import TYPE_AUDIO, TYPE_VIDEO, decode, encode, Header  # noqa: E402
from safety import classical_vote  # noqa: E402
from voice import CAL_FRAMES, END_FRAMES, MIN_UTTERANCE_BYTES, Voice, pcm16k, rms, tone  # noqa: E402


class Protocol(unittest.TestCase):
    def test_audio_video(self):
        pcm = b"\x00\x01" * 10
        buf = encode(Header(TYPE_AUDIO, 16, 1, 2), pcm)
        h, p = decode(buf)
        self.assertEqual(h.type, TYPE_AUDIO)
        self.assertEqual(p, pcm)
        jpeg = b"\xff\xd8\xff\xd9"
        buf = encode(Header(TYPE_VIDEO, 2, 3, 4), jpeg)
        h, p = decode(buf)
        self.assertEqual(h.type, TYPE_VIDEO)
        self.assertEqual(p, jpeg)


class Faces(unittest.TestCase):
    def test_enroll_match(self):
        path = os.path.join(tempfile.gettempdir(), f"faces-{os.getpid()}.db")
        try:
            os.remove(path)
        except OSError:
            pass
        db = FaceDB(path)
        jpeg = bytes(range(256)) * 8
        rec = db.enroll("Ada", jpeg)
        self.assertEqual(rec["name"], "Ada")
        name, score = db.match(jpeg)
        self.assertEqual(name, "Ada")
        self.assertGreater(score, 0.99)
        names = [f["name"] for f in db.list()]
        self.assertIn("Ada", names)
        self.assertTrue(db.delete(rec["id"]))
        name, _ = db.match(jpeg)
        self.assertIsNone(name)

    def test_embed_stable(self):
        a = embed(b"hello-jpeg-bytes" * 20)
        b = embed(b"hello-jpeg-bytes" * 20)
        self.assertGreater(cosine(a, b), 0.99)


class SafetyEdge(unittest.TestCase):
    def test_classical_cliff(self):
        self.assertEqual(classical_vote({"cliffs": (10, 400, 400, 400)}), STOP)
        self.assertEqual(classical_vote({"cliffs": (400, 400, 400, 400)}), 0)

    def test_explorer_edge_vote(self):
        ex = Explorer()
        sensor = {"cliffs": (400, 400, 400, 400), "on_charger": False}
        self.assertEqual(ex.step(sensor, 0, STOP), STOP)
        ex = Explorer()
        self.assertEqual(ex.step(sensor, 0, BACK_OFF), BACK_OFF)

    def test_veto_wins(self):
        ex = Explorer()
        sensor = {"cliffs": (400, 400, 400, 400), "on_charger": False}
        self.assertEqual(ex.step(sensor, 1, BACK_OFF), STOP)

    def test_charger_looks_not_creep(self):
        ex = Explorer()
        sensor = {"cliffs": (400, 400, 400, 400), "on_charger": True}
        got = ex.step(sensor, 0, 0)
        self.assertIn(got, (LOOK_UP, LOOK_DOWN))


class VoiceVAD(unittest.TestCase):
    def test_silence_no_utterance(self):
        v = Voice()
        v.key = ""
        quiet = b"\x00\x00" * 320
        self.assertIsNone(v.push(quiet))
        self.assertFalse(v.thinking)

    def test_tone_energy(self):
        t = tone(440, 200)
        self.assertGreater(rms(t), 100)
        self.assertGreater(len(t), 1000)

    def test_utterance_is_pcm_not_api(self):
        v = Voice()
        v.key = "should-not-be-used"
        v.cal_frames = CAL_FRAMES
        loud = tone(440, 20)
        for _ in range(5):
            self.assertIsNone(v.push(loud))
        self.assertTrue(v.active)
        quiet = b"\x00\x00" * 320
        for _ in range(END_FRAMES - 1):
            self.assertIsNone(v.push(quiet))
        got = v.push(quiet)
        self.assertIsInstance(got, bytes)
        self.assertGreater(len(got), 1000)
        # pre-roll: the five onset packets are all in the clip
        self.assertTrue(got.startswith(loud * 5))
        self.assertTrue(v.thinking)


    def test_noise_does_not_chase_onset(self):
        v = Voice()
        v.cal_frames = CAL_FRAMES
        v.noise = 1000
        loud = tone(440, 20)
        for _ in range(4):
            self.assertIsNone(v.push(loud))
        self.assertLess(v.noise, 1500)
        self.assertFalse(v.active)

    def test_short_clip_is_below_min(self):
        self.assertGreater(MIN_UTTERANCE_BYTES, 6400)

    def test_resample_24k_to_16k(self):
        pcm = tone(440, 300, rate=24000)
        out = pcm16k(pcm, 24000)
        self.assertEqual(len(out), (len(pcm) // 2) * 16000 // 24000 * 2)
        self.assertEqual(pcm16k(tone(440, 20), 16000), tone(440, 20))


class CRC(unittest.TestCase):
    def test_hub_crc_matches_go(self):
        # encode then flip last byte
        buf = encode(Header(1, 0, 1, 1), b"abc")
        bad = bytearray(buf)
        bad[-1] ^= 0xFF
        with self.assertRaises(ValueError):
            decode(bytes(bad))
        crc = zlib.crc32(buf[:-4]) & 0xFFFFFFFF
        self.assertEqual(struct.unpack("<I", buf[-4:])[0], crc)



class Dedupe(unittest.TestCase):
    def test_udp_tcp_copies_dropped(self):
        from main import SeqDedupe
        d = SeqDedupe(window=64)
        self.assertTrue(d.first(1))
        self.assertTrue(d.first(2))
        self.assertFalse(d.first(1))
        self.assertFalse(d.first(2))
        for s in range(3, 500):
            self.assertTrue(d.first(s))
            self.assertFalse(d.first(s))
        # agent restart: counter starts again
        self.assertTrue(d.first(1))


class DateAndSearch(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("HUB_TZ", "HUB_CITY", "HUB_COUNTRY", "HUB_WEB_SEARCH")}

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_date_context_local_zone(self):
        import datetime as dt
        from voice import now_context, system_prompt
        os.environ["HUB_TZ"] = "Europe/Berlin"
        os.environ.pop("HUB_CITY", None)
        os.environ.pop("HUB_COUNTRY", None)
        utc = dt.datetime(2026, 10, 9, 22, 30, tzinfo=dt.timezone.utc)  # 00:30 Saturday in Berlin
        ctx = now_context(utc)
        self.assertIn("Saturday, 10 October 2026, 00:30", ctx)
        self.assertIn("UTC+02:00", ctx)
        self.assertIn("2026-10-10", ctx)
        winter = now_context(dt.datetime(2026, 12, 24, 12, 0, tzinfo=dt.timezone.utc))
        self.assertIn("UTC+01:00", winter)
        self.assertIn("Thursday, 24 December 2026, 13:00", winter)
        os.environ["HUB_CITY"] = "Berlin"
        self.assertIn("The robot is in Berlin.", system_prompt(utc))

    def test_bad_zone_falls_back_to_utc(self):
        import datetime as dt
        from voice import now_context
        os.environ["HUB_TZ"] = "Mars/Olympus"
        self.assertIn("(UTC, UTC+00:00)", now_context(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)))

    def test_speakable_strips_citations(self):
        from voice import speakable
        raw = (
            "**Berlin** is 14°C and cloudy right now "
            "([wetter.com](https://www.wetter.com/berlin?utm_source=openai)). "
            "See [DWD](https://dwd.de) or https://example.com/x for more 【3†source】 [1]."
        )
        out = speakable(raw)
        self.assertEqual(out, "Berlin is 14°C and cloudy right now. See DWD or for more.")
        for bad in ("http", "www", "**", "【", "[1]", "("):
            self.assertNotIn(bad, out)

    def test_responses_body_parsing(self):
        from voice import _responses_text
        body = {"output": [
            {"type": "web_search_call", "status": "completed"},
            {"type": "message", "content": [{"type": "output_text", "text": "It is sunny.", "annotations": []}]},
        ]}
        self.assertEqual(_responses_text(body), ("It is sunny.", True))
        self.assertEqual(_responses_text({"output": []}), ("", False))

    def test_search_failure_falls_back_to_chat(self):
        import urllib.error
        v = Voice()
        v.key = "test"
        v.web_search = True
        calls = []

        def fake_post(url, payload, timeout):
            calls.append(url)
            if url.endswith("/responses"):
                raise urllib.error.URLError("timed out")
            self.assertIn("Local clock:", payload["messages"][0]["content"])
            return {"choices": [{"message": {"content": "Today is Friday [1]."}}]}

        v._post = fake_post
        self.assertEqual(v.chat("what day is it"), "Today is Friday.")
        self.assertEqual(calls[0][-10:], "/responses")
        self.assertTrue(calls[1].endswith("/chat/completions"))
        self.assertEqual(v.last_via, "chat")
        self.assertFalse(v.last_searched)

    def test_search_reply_used_and_flagged(self):
        v = Voice()
        v.key = "test"
        v.web_search = True

        def fake_post(url, payload, timeout):
            self.assertTrue(url.endswith("/responses"))
            self.assertEqual(payload["tools"][0]["type"], "web_search")
            self.assertIn("Local clock:", payload["instructions"])
            return {"output": [
                {"type": "web_search_call"},
                {"type": "message", "content": [{"type": "output_text", "text": "Rain in Berlin ([x](https://x.de))."}]},
            ]}

        v._post = fake_post
        self.assertEqual(v.chat("weather in Berlin"), "Rain in Berlin.")
        self.assertTrue(v.last_searched)
        self.assertEqual(v.last_via, "responses")

    def test_search_off_uses_chat_only(self):
        v = Voice()
        v.key = "test"
        v.web_search = False
        v._post = lambda url, payload, timeout: {"choices": [{"message": {"content": "Hi."}}]}
        self.assertEqual(v.chat("hi"), "Hi.")

class ThinkingTurn(unittest.TestCase):
    """Thinking covers VAD end -> STT -> chat/search -> TTS, then speech goes
    out before idle in one batch; every error path still clears it."""

    def setUp(self):
        import main
        self.m = main
        main.pop_cmds()
        with main.LOCK:
            main.STATE["thinking"] = False
            main.STATE["voice_busy"] = False
            main.THINK.update(t0=0.0, sent=0.0, why="")
        self.saved = (main.VOICE.transcribe, main.VOICE.chat, main.VOICE.tts)
        self.seen = []

    def tearDown(self):
        m = self.m
        m.VOICE.transcribe, m.VOICE.chat, m.VOICE.tts = self.saved
        m.pop_cmds()

    def faceui(self):
        return [(k, p) for k, p in self.m.pop_cmds()]

    def stub(self, text="hi", reply="hello", audio=b"\x01\x00" * 8, fail=None):
        m = self.m

        def busy(tag):
            with m.LOCK:
                self.seen.append((tag, m.STATE["thinking"], m.STATE["voice_busy"]))

        def stt(pcm):
            busy("stt")
            if fail == "stt":
                raise RuntimeError("boom")
            return text

        def chat(t):
            busy("chat")
            if fail == "chat":
                raise ValueError("bad json")
            return reply

        def tts(r):
            busy("tts")
            if fail == "tts":
                raise KeyError("x")
            return audio

        m.VOICE.transcribe, m.VOICE.chat, m.VOICE.tts = stt, chat, tts

    def test_on_during_every_call_speak_before_idle(self):
        m = self.m
        self.stub()
        self.assertTrue(m.begin_think("vad"))
        m.run_turn(b"\x00" * 100)
        self.assertEqual(self.seen, [("stt", True, True), ("chat", True, True), ("tts", True, True)])
        cmds = self.faceui()
        self.assertEqual(cmds[0], (m.CMD_FACEUI, b"thinking|"))
        self.assertEqual([k for k, _ in cmds[-2:]], [m.CMD_SPEAK, m.CMD_FACEUI])
        self.assertEqual(cmds[-1][1], b"idle|")
        self.assertFalse(m.STATE["thinking"])
        self.assertFalse(m.STATE["voice_busy"])

    def test_errors_always_clear(self):
        m = self.m
        for fail in ("stt", "chat", "tts"):
            self.stub(fail=fail)
            m.begin_think("vad")
            m.run_turn(b"\x00" * 100)
            cmds = self.faceui()
            self.assertEqual(cmds[-1], (m.CMD_FACEUI, b"idle|"), fail)
            self.assertNotIn(m.CMD_SPEAK, [k for k, _ in cmds], fail)
            self.assertFalse(m.STATE["thinking"], fail)
            self.assertFalse(m.STATE["voice_busy"], fail)

    def test_empty_transcript_and_reply_clear(self):
        m = self.m
        for text, reply in (("", ""), ("hi", "")):
            self.stub(text=text, reply=reply)
            m.run_turn(b"\x00" * 100)  # also opens the turn itself
            cmds = self.faceui()
            self.assertEqual(cmds[0], (m.CMD_FACEUI, b"thinking|"))
            self.assertEqual(cmds[-1], (m.CMD_FACEUI, b"idle|"))
            self.assertFalse(m.STATE["voice_busy"])

    def test_keepalive_refreshes_then_gives_up(self):
        m = self.m
        m.begin_think("vad")
        t0 = m.THINK["t0"]
        self.faceui()
        self.assertFalse(m.think_keepalive(t0 + 1))
        self.assertTrue(m.think_keepalive(t0 + m.THINK_REFRESH_S + 0.1))
        # robot offline: refresh not piled up
        self.assertFalse(m.think_keepalive(t0 + 2 * m.THINK_REFRESH_S + 0.2))
        self.faceui()
        self.assertTrue(m.think_keepalive(t0 + 3 * m.THINK_REFRESH_S))
        self.faceui()
        # slow TTS (~30 s) still covered
        self.assertTrue(m.think_keepalive(t0 + 31))
        self.faceui()
        # hung turn: stop refreshing so the robot clears on its own
        self.assertFalse(m.think_keepalive(t0 + m.THINK_MAX_S + 1))
        m.end_think()
        self.assertFalse(m.think_keepalive(t0 + 40))

    def test_no_refresh_after_end(self):
        m = self.m
        m.begin_think("vad")
        m.end_think(b"\x01\x00", "done")
        self.faceui()
        self.assertFalse(m.think_keepalive())
        self.assertEqual(self.faceui(), [])

    def test_second_turn_rejected_while_busy(self):
        m = self.m
        self.assertTrue(m.begin_think("vad"))
        self.assertFalse(m.begin_think("face"))
        m.end_think()

    def test_say_owns_turn_and_clears_on_error(self):
        m = self.m
        self.stub(fail="tts")
        self.assertEqual(m.speak_turn("hello", "say"), 0)
        cmds = self.faceui()
        self.assertEqual(cmds, [(m.CMD_FACEUI, b"thinking|"), (m.CMD_FACEUI, b"idle|")])
        self.stub()
        self.assertGreater(m.speak_turn("hello", "say"), 0)
        cmds = self.faceui()
        self.assertEqual([k for k, _ in cmds], [m.CMD_FACEUI, m.CMD_SPEAK, m.CMD_FACEUI])
        self.assertFalse(m.STATE["voice_busy"])

    def test_face_greeting_thinks_then_shows_name(self):
        m = self.m
        self.stub()
        m.speak_turn("Ann", "face", b"name|Ann")
        self.assertEqual(self.faceui(), [(m.CMD_FACEUI, b"thinking|"), (m.CMD_FACEUI, b"name|Ann"), (m.CMD_SPEAK, b"\x01\x00" * 8)])
        self.assertFalse(m.STATE["thinking"])

    def test_say_during_turn_leaves_face_alone(self):
        m = self.m
        self.stub()
        m.begin_think("vad")
        self.faceui()
        m.speak_turn("hello", "say")
        self.assertEqual([k for k, _ in self.faceui()], [m.CMD_SPEAK])
        self.assertTrue(m.STATE["thinking"])
        m.end_think()


if __name__ == "__main__":
    unittest.main()
