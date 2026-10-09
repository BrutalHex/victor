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
from voice import CAL_FRAMES, MIN_UTTERANCE_BYTES, Voice, pcm16k, rms, tone  # noqa: E402


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
        for _ in range(7):
            self.assertIsNone(v.push(quiet))
        got = v.push(quiet)
        self.assertIsInstance(got, bytes)
        self.assertGreater(len(got), 1000)
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

if __name__ == "__main__":
    unittest.main()
