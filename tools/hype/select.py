"""Pure scoring and window selection. No file or media access, so it is fully unit-tested.

Per-second signals are numpy arrays in 0..1. A signal that is None is dropped and the
remaining weights are re-scaled (a dub has no subtitles).
"""
from __future__ import annotations

import numpy as np

WEIGHTS = {"loud": 0.17, "swell": 0.13, "speech": 0.15, "bass": 0.08, "cuts": 0.15, "motion": 0.15, "subs": 0.17}
LENGTHS = (30, 35, 40, 45)
GAP = 10          # seconds kept free around a chosen window
SNAP = 3.0        # how far an end may move to reach a scene cut
PEAK_SHARE = 0.3  # window value = (1 - share) * average + share * peak


def normalize(x: np.ndarray) -> np.ndarray:
    """Scale to 0..1 with the 5th/95th percentile, so one spike cannot flatten the rest."""
    x = np.asarray(x, dtype=float)
    lo, hi = np.percentile(x, [5, 95])
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def smooth(x: np.ndarray, width: int = 5) -> np.ndarray:
    if width <= 1:
        return np.asarray(x, dtype=float)
    return np.convolve(x, np.ones(width) / width, mode="same")


def combine(signals: dict[str, np.ndarray | None], weights: dict[str, float] = WEIGHTS) -> np.ndarray:
    """Weighted sum of the signals that exist, weights re-scaled to total 1."""
    used = {k: v for k, v in signals.items() if v is not None and k in weights}
    if not used:
        raise ValueError("no signals")
    total = sum(weights[k] for k in used)
    n = len(next(iter(used.values())))
    out = np.zeros(n)
    for k, v in used.items():
        out += weights[k] / total * normalize(v)
    return out


def allowed_mask(n: int, excluded: list[tuple[float, float]]) -> np.ndarray:
    ok = np.ones(n, dtype=bool)
    for a, b in excluded:
        ok[max(0, int(np.floor(a))):min(n, int(np.ceil(b)))] = False
    return ok


def best_windows(score: np.ndarray, ok: np.ndarray, top: int = 3, lengths=LENGTHS, gap: int = GAP) -> list[dict]:
    """Greedy top windows: best first, then the best that does not touch it (plus gap)."""
    s = smooth(score)
    free = ok.copy()
    csum = np.concatenate([[0.0], np.cumsum(s)])
    found: list[dict] = []
    for _ in range(top):
        best = None
        for length in lengths:
            for start in range(0, len(s) - length + 1):
                if not free[start:start + length].all():
                    continue
                avg = (csum[start + length] - csum[start]) / length
                peak = s[start:start + length].max()
                value = (1 - PEAK_SHARE) * avg + PEAK_SHARE * peak
                if best is None or value > best["value"]:
                    best = {"start": float(start), "end": float(start + length), "value": float(value), "avg": float(avg), "peak": float(peak)}
        if best is None:
            break
        found.append(best)
        free[max(0, int(best["start"]) - gap):min(len(free), int(best["end"]) + gap)] = False
    return found


def snap(window: dict, cuts: list[float], lo: float = 28, hi: float = 48, reach: float = SNAP) -> dict:
    """Move each end to the nearest scene cut within `reach`, keeping the length in [lo, hi]."""
    def near(t: float) -> float:
        c = [x for x in cuts if abs(x - t) <= reach]
        return min(c, key=lambda x: abs(x - t)) if c else t

    start, end = near(window["start"]), near(window["end"])
    if not lo <= end - start <= hi:
        start, end = window["start"], window["end"]
    return {**window, "start": float(start), "end": float(end)}


def cut_rate(cuts: list[float], n: int, span: int = 5) -> np.ndarray:
    """Scene cuts per second inside a centred window of `span` seconds."""
    hits = np.zeros(n)
    for c in cuts:
        if 0 <= int(c) < n:
            hits[int(c)] += 1
    return np.convolve(hits, np.ones(span) / span, mode="same")
