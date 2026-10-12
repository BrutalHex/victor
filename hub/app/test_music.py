"""Dance to the beat, hum along, pet sounds (music.py, dancer.py, animals.py)."""
import os
import unittest
import wave

import numpy as np

import animals
import brain
import intents
import music
import router
from dancer import Dancer

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000


def clicks(bpm, secs=10.0, start=0.137, noise=0.003, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, noise, int(secs * SR))
    t, per = start, 60.0 / bpm
    while t < secs - 0.05:
        i, n = int(t * SR), 400
        x[i:i + n] += 0.6 * np.exp(-np.arange(n) / 60) * np.sin(2 * np.pi * 1500 * np.arange(n) / SR)
        t += per
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def speechish(secs=10.0, seed=1):
    """Irregular voiced syllables (no steady beat): stands in for talk / TV / his TTS."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 0.003, int(secs * SR))
    t = 0.1
    while t < secs - 0.4:
        d = rng.uniform(0.08, 0.3)
        n = int(d * SR)
        f0 = rng.uniform(110, 220)
        tt = np.arange(n) / SR
        x[int(t * SR):int(t * SR) + n] += 0.3 * np.sin(2 * np.pi * f0 * tt) * np.hanning(n)
        t += d + rng.exponential(0.12)
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def wav(name):
    w = wave.open(os.path.join(HERE, "testdata", name))
    return w.readframes(w.getnframes())


class Intents(unittest.TestCase):
    def test_phrases(self):
        cases = {
            "intent_music_dance": ["dance to the music", "dance to the beat", "dance to this", "tanz zur Musik",
                                   "tanz zum Beat", "با آهنگ برقص", "با این آهنگ برقص"],
            "intent_music_hum": ["hum this", "sing along", "sing it back", "summ mit", "sing mit", "زمزمه کن", "اینو زمزمه کن"],
            "intent_animal_sound": ["bark like a dog", "meow like a cat", "purr", "what does the cat say", "chirp like a bird",
                                    "wie macht der Hund", "miau mal", "schnurr mal", "مثل سگ پارس کن", "میو میو کن", "خرخر کن"],
            "intent_imperative_dance": ["dance", "tanz", "برقص"],
        }
        for want, phrases in cases.items():
            for p in phrases:
                m = intents.match(p)
                self.assertIsNotNone(m, p)
                self.assertEqual(m.name, want, p)

    def test_animal_names(self):
        for t, want in {"bark like a dog": "dog", "wie macht der hund": "dog", "مثل سگ پارس کن": "dog",
                        "میو میو کن": "cat", "meow": "cat", "purr": "purr", "خرخر": "purr", "vogel": "bird",
                        "squeak like a hamster": "hamster"}.items():
            self.assertEqual(animals.name_of(t), want, t)


class Beat(unittest.TestCase):
    def test_click_tracks(self):
        for bpm in (80, 100, 120, 128, 150, 170):
            b = music.BeatTracker()
            b.push(clicks(bpm))
            got, conf, _ = b.tempo()
            self.assertLess(abs(got - bpm) / bpm, 0.02, (bpm, got))
            self.assertGreater(conf, 0.5)

    def test_phase_predicts_next_click(self):
        bpm, start, secs = 120, 0.137, 8.0
        b = music.BeatTracker()
        b.push(clicks(bpm, secs, start))
        _, beats = b.next_beats()
        per = 60 / bpm
        nxt = secs + beats[0]
        err = ((nxt - start) / per - round((nxt - start) / per)) * per
        self.assertLess(abs(err), 0.05, err)

    def test_real_music(self):
        # CC0 clips (testdata/README): librosa reference 117 and 144 BPM
        for f, ref, tol in (("music_rock_cc0.wav", 117.2, 0.06), ("music_electro_cc0.wav", 142.0, 0.05)):
            b = music.BeatTracker()
            b.push(wav(f))
            got, conf, _ = b.tempo()
            self.assertLess(abs(got - ref) / ref, tol, (f, got))
            self.assertGreater(conf, 0.3, f)

    def _gate(self, pcm):
        b, g = music.BeatTracker(), music.MusicGate()
        playing = False
        for i in range(0, len(pcm), SR):  # 0.5 s steps
            b.push(pcm[i:i + SR])
            bpm, conf, _ = b.tempo()
            playing = g.update(bpm, conf, float(np.median(b.rms[-200:])) if len(b.rms) else 0)
        return playing

    def test_gate_music_vs_not(self):
        self.assertTrue(self._gate(wav("music_electro_cc0.wav")))
        self.assertTrue(self._gate(clicks(110, 12)))
        self.assertFalse(self._gate(speechish(12)))
        self.assertFalse(self._gate(np.random.default_rng(2).normal(0, 300, 12 * SR).astype("<i2").tobytes()))
        self.assertFalse(self._gate(bytes(12 * SR * 2)))


class Bars(unittest.TestCase):
    def steps(self, plan):
        return [s for s in plan[5:].split(";") if s]

    def test_caps_and_safety(self):
        import random
        for bpm in (70, 100, 128, 175):
            for seed in range(20):
                rng = random.Random(seed)
                p, dur = music.dance_bar(bpm, on_charger=True, wheels=True, rng=rng)
                st = self.steps(p)
                moves = [s for s in st if not s.startswith("face")]
                self.assertLessEqual(len(moves), 12)
                self.assertFalse(any(s.startswith(("liftmm", "turn")) for s in st), p)  # charger: head + eyes only
                p, _ = music.dance_bar(bpm, on_charger=False, wheels=False, rng=rng)
                self.assertFalse(any(s.startswith("turn") for s in self.steps(p)), p)
                p, _ = music.dance_bar(bpm, on_charger=False, wheels=False, head_only=True, rng=rng)
                self.assertTrue(all(s.startswith(("face", "headdeg")) for s in self.steps(p)), p)
                self.assertGreater(dur, 1.0)

    def test_floor_bar_can_turn_small(self):
        import random
        p, _ = music.dance_bar(120, on_charger=False, wheels=True, rng=random.Random(1))
        turns = [abs(float(s.split()[1])) for s in self.steps(p) if s.startswith("turn")]
        self.assertTrue(turns and max(turns) <= 10)

    def test_bar_fits_the_beat(self):
        import random
        p, dur = music.dance_bar(120, on_charger=True, wheels=False, rng=random.Random(3))
        ms = sum(int(s.split()[-1]) for s in self.steps(p) if s.startswith(("headdeg", "liftmm", "wait")))
        self.assertLessEqual(ms / 1000, dur, (ms, dur))  # never overruns its beats


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(awake=True, on_charger=True, speaking=lambda: False):
    clk = FakeClock()
    sent, said, played = [], [], []
    d = Dancer(send_plan=sent.append, on_charger=lambda: on_charger, awake=lambda: awake, speaking=speaking,
               say=lambda k, l: said.append(k), play=played.append, log=lambda m: None, clock=clk,
               schedule=lambda delay, fn: fn())
    return d, clk, sent, said, played


def stream(d, clk, pcm, chunk=1600):
    for i in range(0, len(pcm), chunk * 2):
        clk.t += chunk / SR  # a chunk arrives when its last sample is captured
        d.feed(pcm[i:i + chunk * 2])
        d.tick()


class WhoIsMyPet(unittest.TestCase):
    DOG = ["Who is my doggy?", "Who is my dog?", "Who's my good dog?", "who is my dock", "whos my dog", "Who is my good boy dog?",
           "Wer ist mein Hund?", "Wer ist mein braver Hund?", "سگ من کیه؟", "سگم کیه؟", "who is my duck"]
    CAT = ["Who is my cat?", "Who's my kitty?", "Wer ist meine Katze?", "گربه من کیه؟", "who is my cad"]

    def test_fast_path_and_pet(self):
        import face_id
        for txt, pet in [(t, "dog") for t in self.DOG] + [(t, "cat") for t in self.CAT]:
            m = intents.match(txt)
            self.assertIsNotNone(m, txt)
            self.assertEqual(m.name, "intent_pet_whoismy", txt)
            self.assertEqual(animals.pet_of(m.arg), pet, txt)
            self.assertFalse(face_id.is_identity_question(txt), txt)  # no camera / NVIDIA
            f = router.fast(txt)  # what main uses: STT adds "?" (12 Oct: fell through to the brain)
            self.assertIsNotNone(f, txt)
            self.assertEqual(f.name, "intent_pet_whoismy", txt)

    def test_live_transcripts_12oct(self):
        # M.O's real attempts 16:35-16:51 CEST: all must give the pet answer
        for txt, pet in [("Where is my cat?", "cat"), ("Who is my chat?", "cat"), ("کتی", "cat"), ("Guddagi.", "dog"),
                         ("Who is my catty?", "cat"), ("Who's my Katie?", "cat"), ("Who is my kitty cat?", "cat"),
                         ("Hu is my doggy?", "dog"), ("Who is my doggie?", "dog"), ("Who's my doggo?", "dog"),
                         ("Doggy!", "dog"), ("Kitty?", "cat"), ("Are you my good dog?", "dog"), ("Wo ist meine Katze?", "cat"),
                         ("Bist du meine Mieze?", "cat"), ("Who is my puppy?", "dog"), ("Who is my kitten?", "cat")]:
            f = router.fast(txt)
            self.assertIsNotNone(f, txt)
            self.assertEqual((f.name, animals.pet_of(f.arg)), ("intent_pet_whoismy", pet), txt)
        for txt in ["Who is my friend?", "Where is my phone?", "Are you a robot?", "Who is my mother?", "This word...", "Dag."]:
            f = intents.match(txt)
            self.assertTrue(f is None or f.name != "intent_pet_whoismy", txt)

    def test_identity_still_identity(self):
        import face_id
        for txt in ["who am I", "wer bin ich", "who is my friend"]:
            m = intents.match(txt)
            self.assertTrue(m is None or m.name != "intent_pet_whoismy", txt)
        self.assertTrue(face_id.is_identity_question("who am I"))

    def test_proud_gesture_and_mishear(self):
        g = animals.gesture("dog", True, proud=True)
        self.assertIn("face expr_dog", g)
        self.assertEqual(intents.match("Forge like a dog.").name, "intent_animal_sound")


class BrainPetCommand(unittest.TestCase):
    def test_pet_answer_survives_guards_and_plans(self):
        for text, step, pet in [("Who is my dog?", "pet_answer dog", "dog"), ("Wer ist meine Katze?", "pet_answer cat", "cat"),
                                ("are you my good boy?", {"do": "pet_answer", "name": "puppy"}, "dog")]:
            d, notes = brain.sanitize({"reply": "", "actions": [step], "intent": "intent_names_username_extend"}, text)
            self.assertEqual(len(d["actions"]), 1, notes)  # not "no move asked"
            plan = brain.build_plan(d["actions"], on_charger=True)
            self.assertIn(("pet_answer", pet), plan["hub"])
        self.assertIn("pet_answer", brain.DO)
        self.assertIn("pet_answer <dog|cat>", brain.instructions(""))


class HumOut(unittest.TestCase):
    def test_melody_moved_by_one_octave_factor_into_speaker_band(self):
        # measured: G5 784 Hz came out as its 3rd harmonic, A5 880 Hz clean
        mel = [(392, .5), (440, .5), (523.3, .6), (440, .5), (329.6, .5)]
        k = music.hum_shift(mel)
        self.assertEqual(k, 4.0)
        y = music.beeps(mel)
        self.assertLessEqual(float(np.abs(y).max()), 0.81)
        seg = y[int(0.1 * SR):int(0.4 * SR)]
        f = np.fft.rfftfreq(len(seg), 1 / SR)[np.argmax(np.abs(np.fft.rfft(seg)))]
        self.assertAlmostEqual(f, 392 * 4, delta=8)


class DancerFlow(unittest.TestCase):
    def setUp(self):
        os.environ.pop("HUB_MUSIC_AUTO", None)

    def test_auto_dance_then_stops_when_music_stops_then_cooldown(self):
        d, clk, sent, *_ = make()
        stream(d, clk, clicks(120, 20))
        self.assertEqual(d.mode, "auto")
        bars = [p for p in sent if "headdeg" in p or "wait" in p]
        self.assertGreaterEqual(len(bars), 3)
        self.assertTrue(all("turn" not in p for p in sent))  # auto: never wheels
        stream(d, clk, bytes(SR * 2 * 12))  # silence
        n = len(sent)
        stream(d, clk, clicks(120, 15))  # music again within the cooldown
        self.assertEqual(d.mode, "")
        self.assertEqual(len(sent), n)

    def test_song_ends_stops_within_seconds_despite_motor_noise(self):
        # measured 12 Oct: music 0.05 rms, room 0.0015; the stale 10 s tracker
        # kept him dancing 9 s after the song. Loud flagged motor frames must
        # not hold it either.
        d, clk, sent, *_ = make()
        song = wav("music_electro_cc0.wav")
        stream(d, clk, song + song)
        self.assertEqual(d.mode, "auto")
        rng = np.random.default_rng(1)
        t_end = clk.t
        for k in range(40):  # 4 s of room noise, every other chunk his motors (flagged, 10x louder)
            loud = k % 2 == 0
            x = rng.normal(0, 0.02 if loud else 0.0015, 1600)
            clk.t += 0.1
            d.feed((x * 32767).astype("<i2").tobytes(), loud)
            d.tick()
            if d.mode == "":
                break
        self.assertEqual(d.mode, "")
        self.assertLess(clk.t - t_end, 3.0)

    def test_auto_flag_off(self):
        os.environ["HUB_MUSIC_AUTO"] = "0"
        try:
            d, clk, sent, *_ = make()
            stream(d, clk, clicks(120, 15))
            self.assertEqual(d.mode, "")
            self.assertEqual(sent, [])
        finally:
            os.environ.pop("HUB_MUSIC_AUTO")

    def test_asleep_head_bob_only(self):
        d, clk, sent, said, played = make(awake=False, on_charger=False)
        stream(d, clk, clicks(100, 16))
        self.assertEqual(d.mode, "asleep")
        for p in sent:
            self.assertTrue(all(s.startswith(("headdeg", "wait", "keepbase")) or s == "" for s in p[5:].split(";")), p)
        self.assertEqual((said, played), ([], []))

    def test_voice_dance_listens_then_no_beat(self):
        d, clk, sent, said, _ = make()
        self.assertEqual(d.start_dance("en", wheels=True), "dance_listen")
        stream(d, clk, speechish(14))
        self.assertEqual(d.mode, "")
        self.assertIn("dance_nobeat", said)

    def test_voice_dance_off_charger_turns_small_and_stop(self):
        d, clk, sent, *_ = make(on_charger=False)
        os.environ["HUB_MUSIC_AUTO"] = "0"
        try:
            d.start_dance("en", wheels=True)
            stream(d, clk, clicks(120, 20))
            self.assertEqual(d.mode, "dance")
            self.assertTrue(any("turn" in p for p in sent))
            self.assertTrue(any(p.startswith("plan:keepbase;") for p in sent))
            self.assertTrue(d.stop("button"))
            n = len(sent)
            stream(d, clk, clicks(120, 3))
            self.assertEqual(len(sent), n)
        finally:
            os.environ.pop("HUB_MUSIC_AUTO")

    def test_own_playback_ignored(self):
        d, clk, sent, *_ = make(speaking=lambda: True)
        stream(d, clk, clicks(120, 15))
        self.assertEqual((d.mode, sent), ("", []))

    def test_hum_round_trip(self):
        d, clk, sent, said, played = make()
        d.start_hum("en", 0.0)
        notes = [(392.0, 0.4), (440.0, 0.4), (523.25, 0.6), (440.0, 0.4)]
        t = np.concatenate([0.4 * np.sin(2 * np.pi * f * np.arange(int(s * SR)) / SR) for f, s in notes])
        pcm = (t * 32767).astype("<i2").tobytes() + bytes(SR * 2 * 4)
        stream(d, clk, pcm)
        self.assertEqual(d.mode, "")
        self.assertEqual(len(played), 1)
        got = [round(h) for h, s in music.hum_notes((t * 32767).astype("<i2").tobytes()) if h > 0]
        self.assertEqual(got, [392, 440, 523, 440])

    def test_hum_silence(self):
        d, clk, sent, said, played = make()
        d.start_hum("en", 0.0)
        stream(d, clk, bytes(SR * 2 * 7))
        self.assertEqual((said, played), (["hum_none"], []))


class Pets(unittest.TestCase):
    def test_synth(self):
        for n in animals.NAMES:
            y = animals.synth(n)
            self.assertGreater(len(y) / SR, 0.3, n)
            self.assertLess(len(y) / SR, 2.0, n)
            self.assertLessEqual(float(np.max(np.abs(y))), 1.0)
            self.assertGreater(float(np.max(np.abs(y))), 0.5)

    def test_gestures(self):
        for n in animals.NAMES:
            on = animals.gesture(n, True)
            self.assertTrue(on.startswith("plan:face "), n)
            self.assertNotIn("liftmm", on)
        self.assertIn("liftmm", animals.gesture("hamster", False))

    def test_brain_tool(self):
        p = brain.build_plan(["animal_sound dog", "expression happy"], "", "", True)
        self.assertIn(("animal_sound", "dog"), p["hub"])
        p = brain.build_plan(["animal_sound bark"], "", "", True)
        self.assertIn(("animal_sound", "dog"), p["hub"])
        p = brain.build_plan(["animal_sound dragon"], "", "", True)
        self.assertEqual(p["hub"], [])
        p = brain.build_plan(["dance_music"], "", "", True)
        self.assertIn(("dance_music", None), p["hub"])
        self.assertIn("animal_sound", brain.RULES)


if __name__ == "__main__":
    unittest.main()


class Timing(unittest.TestCase):
    def test_accent_lands_on_the_beat_and_bars_are_contiguous(self):
        os.environ["HUB_MUSIC_AUTO"] = "0"
        try:
            clk = FakeClock()
            t0 = clk.t
            sends = []
            d = Dancer(send_plan=lambda p: None, on_charger=lambda: True, awake=lambda: True, speaking=lambda: False,
                       say=lambda k, l: None, play=lambda p: None, log=lambda m: None, clock=clk,
                       schedule=lambda delay, fn: sends.append(clk.t + delay))
            d.start_dance("en", wheels=False)
            stream(d, clk, clicks(120, 24, start=0.137))
            self.assertGreaterEqual(len(sends), 5)
            per = 0.5
            for s in sends:
                accent = s + 0.02 + music.ACCENT_S  # HUB_DANCE_LEAD_MS + accent
                ph = ((accent - t0 - 0.137) / per) % 1.0
                err = min(ph, 1 - ph) * per
                self.assertLess(err, 0.04, (s, err))
            gaps = np.diff(sends)
            self.assertTrue(all(abs(g - per) < 0.015 for g in gaps), gaps)  # one plan per beat, no skips
        finally:
            os.environ.pop("HUB_MUSIC_AUTO")

    def test_fast_song_half_time_bars_back_to_back(self):
        os.environ["HUB_MUSIC_AUTO"] = "0"
        try:
            clk = FakeClock()
            sends = []
            d = Dancer(send_plan=lambda p: None, on_charger=lambda: True, awake=lambda: True, speaking=lambda: False,
                       say=lambda k, l: None, play=lambda p: None, log=lambda m: None, clock=clk,
                       schedule=lambda delay, fn: sends.append(clk.t + delay))
            d.start_dance("en", wheels=False)
            stream(d, clk, clicks(140, 24))
            gaps = np.diff(sends)
            half = 2 * 60 / 140  # half time
            self.assertTrue(len(gaps) >= 8 and all(abs(g - half) < 0.015 for g in gaps), gaps)
        finally:
            os.environ.pop("HUB_MUSIC_AUTO")

    def test_motor_noise_frames_do_not_fake_a_beat(self):
        b = music.BeatTracker()
        pcm = speechish(10)
        for i in range(0, len(pcm), 3200):
            b.push(pcm[i:i + 3200], noisy=True)
        self.assertLess(b.tempo()[1], 0.3)
