import numpy as np
import pytest

from tools.hype import select as S


def test_normalize_flat_is_zero():
    assert S.normalize(np.ones(50)).max() == 0


def test_combine_drops_missing_and_rescales():
    a = np.linspace(0, 1, 100)
    out = S.combine({"loud": a, "subs": None})
    assert out.shape == (100,)
    assert out.max() == pytest.approx(1.0, abs=0.05)


def test_best_window_finds_the_hot_stretch_in_the_middle():
    score = np.zeros(600)
    score[300:340] = 1.0
    ok = np.ones(600, dtype=bool)
    w = S.best_windows(score, ok, top=1)[0]
    assert 295 <= w["start"] and w["end"] <= 345


def test_excluded_zone_is_never_chosen():
    score = np.zeros(600)
    score[10:60] = 1.0          # hot, but inside the opening
    score[400:440] = 0.6
    ok = S.allowed_mask(600, [(0, 100)])
    w = S.best_windows(score, ok, top=1)[0]
    assert w["start"] >= 100


def test_windows_do_not_overlap_and_respect_gap():
    score = np.zeros(900)
    score[100:140] = 1.0
    score[150:190] = 0.9
    score[500:540] = 0.8
    ws = S.best_windows(score, np.ones(900, dtype=bool), top=3)
    ws.sort(key=lambda w: w["start"])
    for a, b in zip(ws, ws[1:]):
        assert b["start"] - a["end"] >= S.GAP


def test_snap_moves_to_cuts_and_keeps_length():
    w = {"start": 100.0, "end": 140.0}
    out = S.snap(w, [98.5, 141.5])
    assert out["start"] == 98.5 and out["end"] == 141.5
    far = S.snap(w, [90.0, 200.0])
    assert (far["start"], far["end"]) == (100.0, 140.0)


def test_snap_rejects_a_snap_that_breaks_length():
    out = S.snap({"start": 100.0, "end": 130.0}, [102.9, 127.1])   # would give 24.2 s
    assert (out["start"], out["end"]) == (100.0, 130.0)


def test_cut_rate_counts_cuts():
    r = S.cut_rate([10.2, 10.8, 11.5], 30)
    assert r[10] > r[0] == 0
