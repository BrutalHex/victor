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
    "cat": "cat", "meow": "cat", "miau": "cat", "kitten": "cat", "katze": "cat", "گربه": "cat", "میو": "cat", "میاو": "cat",
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


def _meow(rng) -> np.ndarray:
    n = int(0.62 * SR)
    t = np.linspace(0, 1, n)
    f = 520 + 330 * np.sin(np.pi * np.clip(t * 1.25, 0, 1)) - 120 * t  # rise then fall
    v = _osc(f)
    # "mi" -> "a" -> "u": formant slides
    out = np.zeros(n)
    for k, (f1, f2) in enumerate(((700, 2300), (1000, 1600), (600, 1000))):
        w = np.clip(1 - abs(t * 2 - k), 0, 1)
        out += w * (_formant(v, f1, 4) + 0.7 * _formant(v, f2, 6))
    return out * _env(n, 0.04, 0.15)


def _purr(rng) -> np.ndarray:
    n = int(1.4 * SR)
    t = np.arange(n) / SR
    am = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 26 * t))  # 26 Hz flutter
    v = _osc(np.full(n, 110.0)) * 0.5 + 0.5 * rng.standard_normal(n)
    v = signal.sosfilt(signal.butter(2, 600, "lowpass", fs=SR, output="sos"), v)
    return v * am * _env(n, 0.15, 0.3) * (0.7 + 0.3 * np.sin(2 * np.pi * 0.7 * t))


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
    return vectorize16(x)


def vectorize16(x: np.ndarray) -> np.ndarray:
    """The voice chain at 16 kHz without its pitch shift (comb + speaker EQ)."""
    import voicefx
    cfg = dict(voicefx.settings(), pitch=0.0, tempo=1.0)
    y = voicefx.vectorize(x, SR, cfg)
    t = np.arange(len(y)) / SR
    return y * (0.85 + 0.15 * np.sin(2 * np.pi * 55 * t))  # light robot ring


def pcm(name: str, seed: int = 0) -> bytes:
    y = synth(name, seed)
    return (np.clip(y, -1, 1) * 32000).astype("<i2").tobytes()


# face + head/lift gesture per sound (plan text; lift dropped on the charger)
GESTURE = {
    "dog": ("expr_excited", "headdeg 8 90;headdeg 0.3 120;wait 60;headdeg 8 90;headdeg 0 160"),
    "cat": ("expr_curious", "headdeg 10 300;wait 300;headdeg 0 400"),
    "purr": ("expr_love", "headdeg -6 500;wait 600;headdeg 0 500"),
    "bird": ("expr_happy", "headdeg 6 100;headdeg 1 100;headdeg 6 100;headdeg 0 200"),
    "hamster": ("expr_excited", "liftmm 6 100;liftmm 0.5 100;liftmm 6 100;liftmm 0 200"),
}


def gesture(name: str, on_charger: bool | None) -> str:
    face, steps = GESTURE.get(name, ("", ""))
    if not face:
        return ""
    st = steps.split(";")
    if on_charger is not False:
        st = [s for s in st if not s.startswith("liftmm")] or ["headdeg 6 100", "headdeg 0 200"]
    return "plan:" + ";".join([f"face {face}"] + st)
