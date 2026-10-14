"""Vector-style voice on the hub (option c: imitation, no Acapela / vic-anim).

Stock Vector speaks Acapela "Ben" (co-USEnglish-Bendnn-22khz) with speed 80 /
shaping 130 (tts_config.json) and raises the pitch in its audio layer. Here:
OpenAI TTS (24 kHz PCM) ->

  1. pitch + formant shift up by HUB_VOICE_PITCH semitones (resample, so the
     formants rise with the pitch: a smaller "head"), duration restored and
     slowed by HUB_VOICE_TEMPO with WSOLA time-stretch;
  2. robot character (HUB_VOICE_FX=1): a short feed-forward comb (metallic
     ring) plus a light ring-modulator warble;
  3. small-speaker EQ: high-pass, presence peak, low-pass;
  4. peak-normalised to -1 dBFS (no clipping).

All numpy/scipy, ~10-40 ms per second of speech on the hub.
"""

from __future__ import annotations

import os

import numpy as np
from scipy import signal

SR = 24000


def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def settings() -> dict:
    return {
        "pitch": _envf("HUB_VOICE_PITCH", 4.0),  # semitones up
        "tempo": _envf("HUB_VOICE_TEMPO", 0.94),  # <1 = slower (Acapela speed 80)
        "fx": os.environ.get("HUB_VOICE_FX", "1").strip().lower() not in ("0", "false", "off", "no"),
        "comb_ms": _envf("HUB_VOICE_COMB_MS", 4.5),
        "comb_mix": _envf("HUB_VOICE_COMB_MIX", 0.28),
        "ring_hz": _envf("HUB_VOICE_RING_HZ", 55.0),
        "ring_mix": _envf("HUB_VOICE_RING_MIX", 0.0),
    }


def wsola(x: np.ndarray, stretch: float, n: int = 1024, tol: int = 256) -> np.ndarray:
    """Time-stretch by `stretch` (2.0 = twice as long) without changing pitch."""
    if abs(stretch - 1.0) < 1e-3 or len(x) < 2 * n:
        return x.copy()
    hs = n // 2
    ha = hs / stretch
    win = np.hanning(n)
    out_len = int(len(x) * stretch) + n
    y = np.zeros(out_len)
    norm = np.zeros(out_len)
    xp = np.concatenate([np.zeros(tol), x, np.zeros(n + tol + 2 * hs)])
    prev = 0  # input position (in padded coords, minus tol) of the last frame
    k = 0
    while True:
        out_pos = k * hs
        nominal = int(round(k * ha))
        if nominal >= len(x) or out_pos + n > out_len:
            break
        if k == 0:
            best = 0
        else:
            # natural continuation of the previous frame
            nat = xp[tol + prev + hs: tol + prev + hs + n]
            lo = nominal - tol
            seg = xp[tol + lo: tol + lo + n + 2 * tol]
            if len(seg) < n + 2 * tol:
                break
            c = signal.correlate(seg, nat, mode="valid")
            best = lo + int(np.argmax(c))
        frame = xp[tol + best: tol + best + n]
        if len(frame) < n:
            break
        y[out_pos: out_pos + n] += frame * win
        norm[out_pos: out_pos + n] += win
        prev = best
        k += 1
    norm[norm < 1e-6] = 1.0
    y = y / norm
    return y[: int(len(x) * stretch)]


def _peaking(f0: float, gain_db: float, q: float, sr: int) -> tuple[np.ndarray, np.ndarray]:
    a = 10 ** (gain_db / 40)
    w = 2 * np.pi * f0 / sr
    alpha = np.sin(w) / (2 * q)
    b = np.array([1 + alpha * a, -2 * np.cos(w), 1 - alpha * a])
    den = np.array([1 + alpha / a, -2 * np.cos(w), 1 - alpha / a])
    return b / den[0], den / den[0]


def vectorize(x: np.ndarray, sr: int = SR, cfg: dict | None = None) -> np.ndarray:
    """float [-1, 1] mono in, float mono out at the same rate."""
    cfg = cfg or settings()
    x = np.asarray(x, dtype=np.float64)
    if len(x) == 0:
        return x
    ratio = 2 ** (cfg["pitch"] / 12.0)
    tempo = max(0.5, min(2.0, cfg["tempo"]))
    y = x
    if abs(ratio - 1) > 1e-3 or abs(tempo - 1) > 1e-3:
        # stretch so that after resampling by 1/ratio the length is len/tempo
        y = wsola(x, ratio / tempo)
        if abs(ratio - 1) > 1e-3:
            up, down = _frac(ratio)
            y = signal.resample_poly(y, down, up)  # shorter by ratio -> pitch up
    if cfg["fx"]:
        d = max(1, int(sr * cfg["comb_ms"] / 1000))
        comb = np.zeros_like(y)
        comb[d:] = y[:-d]
        y = (1 - cfg["comb_mix"]) * y + cfg["comb_mix"] * comb
        t = np.arange(len(y)) / sr
        y = y * ((1 - cfg["ring_mix"]) + cfg["ring_mix"] * np.sin(2 * np.pi * cfg["ring_hz"] * t))
    # small-speaker EQ
    sos_hp = signal.butter(2, 220, "highpass", fs=sr, output="sos")
    sos_lp = signal.butter(4, min(6800, 0.45 * sr), "lowpass", fs=sr, output="sos")
    y = signal.sosfilt(sos_hp, y)
    b, a = _peaking(3000, 3.0, 1.0, sr)
    y = signal.lfilter(b, a, y)
    y = signal.sosfilt(sos_lp, y)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 1e-9:
        y = y * (0.89 / peak)  # -1 dBFS
    return y


def _frac(r: float, max_den: int = 64) -> tuple[int, int]:
    from fractions import Fraction

    f = Fraction(r).limit_denominator(max_den)
    return f.numerator, f.denominator


def pcm_vectorize(pcm: bytes, sr: int = SR, cfg: dict | None = None) -> bytes:
    """s16le bytes in/out."""
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
    y = vectorize(x, sr, cfg)
    return (np.clip(y, -1, 1) * 32767).astype("<i2").tobytes()


def resample(pcm: bytes, src: int, dst: int) -> bytes:
    """Polyphase resample s16le (anti-aliased, unlike linear interpolation)."""
    if src == dst or not pcm:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    from math import gcd

    g = gcd(src, dst)
    y = signal.resample_poly(x, dst // g, src // g)
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


def f0_median(x: np.ndarray, sr: int, fmin: float = 70, fmax: float = 450) -> float:
    """Median F0 of voiced 40 ms frames (normalised autocorrelation)."""
    x = np.asarray(x, dtype=np.float64)
    n = int(0.04 * sr)
    hop = n // 2
    lo, hi = int(sr / fmax), int(sr / fmin)
    thr = 0.1 * np.max(np.abs(x)) if len(x) else 0
    f0s = []
    for i in range(0, len(x) - n - hi, hop):
        fr = x[i: i + n + hi]
        if np.max(np.abs(fr[:n])) < thr:
            continue
        fr = fr - fr.mean()
        a = fr[:n]
        e0 = np.dot(a, a)
        rs = np.array([np.dot(a, fr[L: L + n]) / (np.sqrt(e0 * np.dot(fr[L: L + n], fr[L: L + n])) + 1e-12) for L in range(lo, hi)])
        best = float(rs.max())
        # smallest lag that is (nearly) as periodic as the best: avoids octave-down errors
        j = int(np.argmax(rs >= 0.95 * best))
        while j + 1 < len(rs) and rs[j + 1] > rs[j]:
            j += 1  # climb to that peak
        lag = lo + j
        if best > 0.6:
            f0s.append(sr / lag)
    return float(np.median(f0s)) if f0s else 0.0


def centroid(x: np.ndarray, sr: int) -> float:
    spec = np.abs(np.fft.rfft(np.asarray(x, dtype=np.float64)))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    return float((spec * freqs).sum() / (spec.sum() + 1e-12))
