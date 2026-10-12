"""Vector-style pet sounds, synthesised (no recordings): dog, cat, purr,
bird, hamster. Each is built from oscillators + filtered noise, then run
through the hub's voice chain EQ/comb (voicefx) so it sounds like Vector's
small speaker, not a real animal. Plus a matching face + head/lift gesture
(plan text, DDL gesture steps). No sample files -> no licences to track.
"""
from __future__ import annotations

import re

import numpy as np
from scipy import signal

SR = 16000

ALIASES = {
    "dog": "dog", "bark": "dog", "woof": "dog", "puppy": "dog", "hund": "dog", "bellen": "dog", "bell": "dog", "wau": "dog",
    "سگ": "dog", "پارس": "dog", "هاپ": "dog",
    "cat": "cat", "meow": "cat", "miu": "cat", "mio": "cat", "miao": "cat", "mew": "cat", "miau": "cat", "kitten": "cat", "katze": "cat", "گربه": "cat", "میو": "cat", "میاو": "cat",
    "purr": "purr", "schnurr": "purr", "schnurren": "purr", "خرخر": "purr",
    "bird": "bird", "chirp": "bird", "tweet": "bird", "vogel": "bird", "zwitschern": "bird", "zwitscher": "bird", "پرنده": "bird", "جیک": "bird",
    "hamster": "hamster", "squeak": "hamster", "همستر": "hamster",
}
NAMES = ["dog", "cat", "purr", "bird", "hamster"]


def name_of(text: str) -> str:
    t = (text or "").lower()
    for k, v in ALIASES.items():
        if re.search(r"(?<!\w)" + re.escape(k), t):
            return v
    return ""


def _env(n: int, a: float, r: float) -> np.ndarray:
    i = np.arange(n)
    return np.clip(np.minimum(i / max(1, a * SR), (n - i) / max(1, r * SR)), 0, 1)


def _osc(f: np.ndarray, kind: str = "saw") -> np.ndarray:
    ph = 2 * np.pi * np.cumsum(f) / SR
    if kind == "sine":
        return np.sin(ph)
    return 2 * ((ph / (2 * np.pi)) % 1.0) - 1  # saw


def _formant(x: np.ndarray, f0: float, q: float = 4.0) -> np.ndarray:
    b, a = signal.iirpeak(f0, q, fs=SR)
    return signal.lfilter(b, a, x)


def _bark(rng) -> np.ndarray:
    n = int(0.16 * SR)
    f = np.linspace(520, 330, n) * (1 + 0.02 * rng.standard_normal())
    v = _osc(f) * 0.7 + 0.5 * rng.standard_normal(n) * np.exp(-np.arange(n) / (0.03 * SR))
    v = _formant(v, 900, 3) + 0.6 * _formant(v, 1900, 5)
    return v * _env(n, 0.005, 0.06)


# Cat: source-filter synthesis lifted into the band his speaker plays
# (measured 12 Oct: almost nothing below ~800 Hz). A harmonic source on a
# pitch contour, each harmonic weighted by moving formant resonances
# (i/e -> a/o -> u), breathy onset, slight vibrato, soft tail. Not run
# through the voice comb (it notched these notes); a light robot buzz instead.
# Formant tracks: (time 0..1, F1, F2, F3) in Hz, already shifted up.
_VOWELS = {"i": (1000, 2900, 3900), "e": (1150, 2500, 3600), "a": (1400, 2000, 3300),
           "o": (1100, 1600, 3100), "u": (950, 1300, 3000)}
MEOWS = {
    # name: (seconds, pitch contour [(t, Hz)], vowel path [(t, vowel)], trill seconds)
    "mew": (0.45, [(0, 950), (0.3, 1350), (1, 1100)], [(0, "i"), (0.5, "e"), (1, "a")], 0.0),
    "meow": (0.8, [(0, 760), (0.35, 1350), (0.7, 1050), (1, 800)], [(0, "i"), (0.3, "e"), (0.55, "a"), (0.8, "o"), (1, "u")], 0.0),
    "mrrp": (0.6, [(0, 820), (0.45, 900), (1, 1350)], [(0, "u"), (0.4, "o"), (1, "a")], 0.22),
}


def _track(pts, t):
    return np.interp(t, [p[0] for p in pts], [p[1] for p in pts])


def meow(variant: str, rng) -> np.ndarray:
    secs, pitch, vowels, trill = MEOWS[variant]
    n = int(secs * SR)
    t = np.linspace(0, 1, n)
    ts = np.arange(n) / SR
    f0 = _track(pitch, t) * (1 + 0.03 * rng.standard_normal())
    f0 = f0 * (1 + 0.018 * np.sin(2 * np.pi * 6.0 * ts) * np.clip((t - 0.25) * 3, 0, 1))  # vibrato after the rise
    vt = [p[0] for p in vowels]
    F = [np.interp(t, vt, [_VOWELS[v][k] for _, v in vowels]) for k in range(3)]
    bw = (220.0, 260.0, 380.0)
    gain = (1.0, 0.75, 0.35)
    ph = 2 * np.pi * np.cumsum(f0) / SR
    out = np.zeros(n)
    for h in range(1, 12):
        fh = h * f0
        a = np.zeros(n)
        for Fk, b, g in zip(F, bw, gain):
            a += g / (1 + ((fh - Fk) / b) ** 2)
        a *= (fh < 7000) / h ** 0.3
        out += a * np.sin(h * ph)
    # breathy onset: noise through the same first formant, fading out
    noise = rng.standard_normal(n) * np.exp(-ts / 0.08)
    noise = signal.sosfilt(signal.butter(2, [900, 4500], "bandpass", fs=SR, output="sos"), noise)
    out = out + 0.35 * noise * np.max(np.abs(out))
    if trill:  # 'mrr': ~28 Hz flutter at the start
        am = 1 - 0.75 * (0.5 + 0.5 * np.sin(2 * np.pi * 28 * ts)) * np.clip(1 - ts / trill, 0, 1)
        out *= am
    out *= _env(n, 0.035, 0.18) * (0.6 + 0.4 * np.sin(np.pi * np.clip(t * 1.3, 0, 1)))
    out *= 0.9 + 0.1 * np.sign(np.sin(2 * np.pi * 70 * ts))  # light robot buzz
    out = signal.sosfilt(signal.butter(2, 750, "highpass", fs=SR, output="sos"), out)
    return out / (np.max(np.abs(out)) + 1e-9) * 0.9


def _meow(rng) -> np.ndarray:
    return meow(str(rng.choice(["mew", "meow", "meow", "mrrp"])), rng)


def _purr(rng) -> np.ndarray:
    """~25 Hz pulsed rumble moved up to 900-2600 Hz, breathing in (softer,
    a bit higher) and out (louder) at ~0.7 Hz."""
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    rate = 25 + 1.5 * np.sin(2 * np.pi * 0.35 * t)
    pulse = 0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(rate) / SR)
    am = pulse ** 1.5  # fuller than ^3: louder through his speaker (12 Oct: purr only 3.8x room level)
    src = _osc(np.full(n, 230.0)) * 0.6 + 0.6 * rng.standard_normal(n)
    v = signal.sosfilt(signal.butter(2, [900, 2600], "bandpass", fs=SR, output="sos"), src)
    breath = 0.8 + 0.2 * np.sin(2 * np.pi * 0.7 * t)
    v = v * am * breath * _env(n, 0.15, 0.3)
    return v / (np.max(np.abs(v)) + 1e-9) * 0.85


def _chirp(rng) -> np.ndarray:
    n = int(0.09 * SR)
    f = np.linspace(2600, 4200, n) + 300 * np.sin(np.linspace(0, 6 * np.pi, n))
    return _osc(f, "sine") * _env(n, 0.005, 0.03)


def _squeak(rng) -> np.ndarray:
    n = int(0.07 * SR)
    f = np.linspace(3200, 2600, n)
    return _osc(f, "sine") * _env(n, 0.003, 0.02)


def _gap(s: float) -> np.ndarray:
    return np.zeros(int(s * SR))


def synth(name: str, seed: int = 0) -> np.ndarray:
    """float mono at 16 kHz, peak ~0.9, Vector-ised."""
    rng = np.random.default_rng(seed)
    parts: list[np.ndarray] = []
    if name == "dog":
        parts = [_bark(rng), _gap(0.12), _bark(rng)]
    elif name == "cat":
        parts = [_meow(rng)]
    elif name == "purr":
        parts = [_purr(rng)]
    elif name == "bird":
        for _ in range(3):
            parts += [_chirp(rng), _gap(0.05), _chirp(rng), _gap(0.18)]
    elif name == "hamster":
        for _ in range(4):
            parts += [_squeak(rng), _gap(0.06)]
    else:
        return np.zeros(0)
    x = np.concatenate(parts)
    if name in ("cat", "purr"):
        return x  # clean: the voice comb muddied these (own light buzz instead)
    return vectorize16(x)


def vectorize16(x: np.ndarray) -> np.ndarray:
    """The voice chain at 16 kHz without its pitch shift (comb + speaker EQ)."""
    import voicefx
    cfg = dict(voicefx.settings(), pitch=0.0, tempo=1.0)
    y = voicefx.vectorize(x, SR, cfg)
    t = np.arange(len(y)) / SR
    return y * (0.85 + 0.15 * np.sin(2 * np.pi * 55 * t))  # light robot ring


def pcm(name: str, seed: int = 0, variant: str = "") -> bytes:
    y = meow(variant, np.random.default_rng(seed)) if variant in MEOWS else synth(name, seed)
    return (np.clip(y, -1, 1) * 32000).astype("<i2").tobytes()


# face + head/lift gesture per sound (plan text; lift dropped on the charger)
GESTURE = {
    "dog": ("expr_dog", "headdeg 8 90;headdeg 0.3 120;wait 60;headdeg 8 90;headdeg 0 160"),
    "cat": ("expr_cat", "headdeg 10 300;wait 300;headdeg 0 400"),
    "purr": ("expr_cat", "headdeg -6 500;wait 600;headdeg 0 500"),
    "bird": ("expr_happy", "headdeg 6 100;headdeg 1 100;headdeg 6 100;headdeg 0 200"),
    "hamster": ("expr_excited", "liftmm 6 100;liftmm 0.5 100;liftmm 6 100;liftmm 0 200"),
}


PET_WORDS = {
    "dog": ["dog", "dogs", "doggy", "doggie", "doggo", "doggies", "dogy", "doge", "dock", "doc", "duck", "dug", "dawg", "dork",
            "puppy", "pup", "puppie", "guddagi", "gudagi", "goodagi", "good doggy", "good doggie", "good dog", "hund", "hundi",
            "hündchen", "wauwau", "hundchen", "سگ", "سگه", "سگم", "داگی", "پاپی", "هاپو"],
    "cat": ["cat", "cats", "catty", "cattie", "catie", "katie", "katy", "kitty", "kittie", "kitten", "kittens", "kit", "kat",
            "kad", "cad", "caddy", "caddie", "kady", "katty", "cut", "chat", "kitty cat", "pussycat", "pussy cat", "good kitty", "katze", "kätzchen", "katzchen",
            "mieze", "miezekatze", "kater", "گربه", "گربهه", "گربم", "گربه ام", "گربه‌ام", "کتی", "کیتی", "پیشی"],
}
_PET_LOOKUP = {w: k for k, ws in PET_WORDS.items() for w in ws}


def pet_word(word: str) -> str:
    """Fuzzy: the word after 'who is my ...' -> 'dog' | 'cat' | ''. Covers STT
    slips seen live (12 Oct: 'chat', 'catty', 'کتی', 'Guddagi'); close
    spellings (edit ratio >= 0.8) count too, other words ('friend') don't."""
    import difflib
    w = re.sub(r"[^\w\s]", "", (word or "").lower()).strip()
    if not w:
        return ""
    if w in _PET_LOOKUP:
        return _PET_LOOKUP[w]
    m = difflib.get_close_matches(w, list(_PET_LOOKUP), n=1, cutoff=0.8)
    if m and len(w) >= 4:
        return _PET_LOOKUP[m[0]]
    for pre, k in (("dogg", "dog"), ("pupp", "dog"), ("kitt", "cat"), ("catt", "cat")):
        if w.startswith(pre):
            return k
    return ""


_CATS = ("cat", "kitt", "kat", "cad", "kit", "pussy", "katz", "kätz", "miez", "kater", "گرب")


def pet_of(word: str) -> str:
    """'who is my <pet>' word (incl. STT slips: dock, duck, cad) -> dog | cat."""
    w = (word or "").lower()
    return "cat" if any(c in w for c in _CATS) else "dog"


def gesture(name: str, on_charger: bool | None, proud: bool = False) -> str:
    face, steps = GESTURE.get(name, ("", ""))
    if proud and face:  # "who is my dog?" - "me!": proud, chin up
        steps = "headdeg 14 250;wait 250;" + steps  # chin up; the dog/cat face stays (expr_proud for others)
        face = face if face in ("expr_dog", "expr_cat") else "expr_proud"
    if not face:
        return ""
    st = steps.split(";")
    if on_charger is not False:
        st = [s for s in st if not s.startswith("liftmm")] or ["headdeg 6 100", "headdeg 0 200"]
    return "plan:" + ";".join([f"face {face}"] + st)
