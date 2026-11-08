"""Pick a manhwa part's thumbnail panels by code (no AI, no API, no tokens) for thumb_edit.py. Runs on GitHub (thumb.yml).

Usage: python thumb_auto.py <part job>

Writes, in <part>/yt/:
  thumb-auto.json   spec for thumb_edit.py: auto-scene, auto-split, auto-trio (also thumb.json when the part has none)
  panel-ranks.json  every panel with its signals and the reason each pick won, plus confidence, timing, and how the
                    hand-picked panels (thumb.json) rank; tune the weights from this file, no rebuild needed

Each panel gets separate 0-100 signals; looks and story stay apart, so a pretty face cannot beat the key moment:
  visual 20%   sharpness, colourfulness, contrast, exposure, naturalness, saliency: fralapo/smart-thumbnailer (MIT),
               downloaded at run time; simple built-in metrics if that fails
  face   20%   how big the biggest anime face is: nagadomi/lbpcascade_animeface, downloaded at run time;
               without it, no face points (the arrow then aims at the saliency peak, then the centre)
  story  25%   the hook's panel 100; panels on *starred* lines more than plain lines; panels the script never shows 0
  frame  15%   crop suitability: a wide panel fills 16:9, a tall one is shown whole, a thin strip is poor
  words -10%   speech bubbles (the build's OCR, work/panels_text.json; without it, the share of pure white): a penalty,
               never a ban, since the line on the panel can be the moment
  variety 10%  given to the SET: split and trio panels are picked to differ (dHash bits apart, time apart)
Low confidence (weak best panel, no faces at all, too few distinct panels) sets "review": true in the spec,
and thumb.yml then says to pick by hand. No panel video at all: one cover thumbnail from AniList, review true.
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PANEL_SECONDS = 3
W, H = 1280, 720
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
SMART_URL = "https://raw.githubusercontent.com/fralapo/smart-thumbnailer/main/thumbnailer.py"
CASCADE_URL = "https://raw.githubusercontent.com/nagadomi/lbpcascade_animeface/master/lbpcascade_animeface.xml"
CACHE = Path.home() / ".cache" / "thumb_auto"
WEIGHTS = {"visual": 0.20, "face": 0.20, "story": 0.25, "frame": 0.15, "words": -0.10}
VARIETY = 0.10
LOOK = {"color": 0.25, "sharp": 0.20, "contrast": 0.15, "natural": 0.10, "exposure": 0.12, "salient": 0.10}
DHASH_MIN = 10        # bits apart for two panels to count as different
POOL = 14             # best panels the sets are built from
TALL = 1.25           # height / width above this: the scene shows the panel whole ("contain")
LOW_SCORE = 45        # best scene under this: ask for a hand pick
THREATS = ("golem", "monster", "zombie", "serpent", "dragon", "demon", "beast", "boss", "king", "giant", "goblin",
           "dokkaebi", "regressor", "witch", "knight", "assassin", "princess", "father", "patriarch", "brother")
STOP = {"The", "Then", "But", "And", "When", "So", "He", "She", "His", "Her", "It", "They", "A", "To", "In", "On",
        "One", "Two", "Now", "If", "Back", "First", "Later", "Next", "Nobody", "Every", "That", "This", "Time", "Inside"}


def fetch(url: str, name: str) -> Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        try:
            urllib.request.urlretrieve(url, path)
        except Exception as exc:
            print(f"  could not download {name}: {exc}")
            return None
    return path


def smart_metrics():
    """smart-thumbnailer's metric functions, or None (then the built-in ones are used)."""
    path = fetch(SMART_URL, "smart_thumbnailer.py")
    if not path:
        return None
    try:
        spec = importlib.util.spec_from_file_location("smart_thumbnailer", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in ("tenengrad_sharpness", "colorfulness_combined", "exposure_score", "mscn_naturalness",
                     "spectral_residual_saliency"):
            getattr(mod, name)
        return mod
    except Exception as exc:
        print(f"  smart-thumbnailer unusable ({exc})")
        return None


def look(bgr: np.ndarray, smart) -> tuple[dict, str]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if smart:
        try:
            sal = smart.spectral_residual_saliency(gray)
            return {"sharp": float(smart.tenengrad_sharpness(gray)), "color": float(smart.colorfulness_combined(bgr)),
                    "contrast": float(gray.std()), "exposure": float(smart.exposure_score(gray)),
                    "natural": float(smart.mscn_naturalness(gray)),
                    "salient": float(np.mean(sal) if isinstance(sal, np.ndarray) else sal)}, "smart-thumbnailer"
        except Exception:
            pass
    b, g, r = cv2.split(bgr.astype("float32"))
    rg, yb = r - g, 0.5 * (r + g) - b
    color = float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))
    mean = float(gray.mean())
    return {"sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()), "color": color, "contrast": float(gray.std()),
            "exposure": 100.0 * max(0.0, 1 - abs(mean - 120) / 120), "natural": 50.0, "salient": 50.0}, "built-in"


def saliency_peak(bgr: np.ndarray) -> tuple[float, float]:
    """Where the eye goes first (spectral residual saliency), as fractions; the centre if it cannot be found."""
    try:
        gray = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (64, 64)).astype("float32")
        spec = np.fft.fft2(gray)
        resid = np.log(np.abs(spec) + 1e-8)
        resid = resid - cv2.blur(resid, (3, 3))
        sal = cv2.GaussianBlur(np.abs(np.fft.ifft2(np.exp(resid + 1j * np.angle(spec)))) ** 2, (9, 9), 2.5)
        y, x = np.unravel_index(np.argmax(sal), sal.shape)
        return (x + 0.5) / 64, (y + 0.5) / 64
    except Exception:
        return 0.5, 0.4


def dhash(bgr: np.ndarray) -> int:
    small = cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (9, 8), interpolation=cv2.INTER_AREA)
    return int("".join("1" if b else "0" for b in (small[:, 1:] > small[:, :-1]).flatten()), 2)


def apart(a: dict, b: dict) -> float:
    """0-1: how different two panels look (dHash) and how far apart they are in the story."""
    bits = bin(a["hash"] ^ b["hash"]).count("1") / 64
    return 0.6 * min(1.0, bits * 2) + 0.4 * min(1.0, abs(a["panel"] - b["panel"]) / 20)


def frames(video: Path, out: Path) -> list[Path]:
    """One frame from the middle of every panel, in one ffmpeg pass."""
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", str(PANEL_SECONDS / 2), "-i", str(video),
                    "-vf", f"fps=1/{PANEL_SECONDS}", "-q:v", "3", str(out / "f%04d.jpg")], check=True)
    return sorted(out.glob("f*.jpg"))


def trim(bgr: np.ndarray) -> np.ndarray:
    ys, xs = np.where(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) > 22)
    return bgr[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(xs) else bgr


def hero_name(segs: list[dict]) -> str:
    """The name the script says most often: the main character."""
    names = Counter(w for s in segs for w in re.findall(r"\b[A-Z][a-z]{2,}\b", s["say"].replace("*", "")) if w not in STOP)
    return names.most_common(1)[0][0] if names else ""


def starred(text: str) -> str:
    return " ".join(re.findall(r"\*([^*]+)\*", text)).strip(" .,!?").upper()


def threat_label(text: str) -> str:
    low = text.lower()
    return next((w.upper() for w in THREATS if w in low), "ENEMY")


def ocr_texts(job: Path) -> dict[int, str]:
    """The build's OCR of each panel; read loosely, since only its length is used."""
    path = job / "work" / "panels_text.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.items() if isinstance(data, dict) else enumerate(data)
        out = {}
        for k, v in items:
            if isinstance(v, dict):
                k, v = v.get("panel", k), v.get("text", "")
            out[int(k)] = v if isinstance(v, str) else " ".join(map(str, v))
        return out
    except Exception as exc:
        print(f"  panels_text.json unreadable ({exc}): white share instead")
        return {}


def story_map(segs: list[dict]) -> tuple[dict[int, float], dict[int, str]]:
    story, line = {}, {}
    for i, seg in enumerate(segs):
        for c in seg["clips"]:
            p = int(float(c[0] if isinstance(c, list) else c) // PANEL_SECONDS)
            value = 100.0 if i == 0 else min(90.0, 30 + 20 * seg["say"].count("*") / 2)
            if value > story.get(p, 0):
                story[p], line[p] = value, seg["say"]
    return story, line


def score_panels(job: Path, work: Path, segs: list[dict]) -> tuple[list[dict], dict]:
    texts = ocr_texts(job)
    story, line = story_map(segs)
    smart = smart_metrics()
    cascade_path = fetch(CASCADE_URL, "lbpcascade_animeface.xml")
    try:  # a broken OpenCV install (two cv2 packages over each other) has no cascades: aim by saliency then
        cascade = cv2.CascadeClassifier(str(cascade_path)) if cascade_path else None
    except Exception as exc:
        print(f"  no face finder ({exc})")
        cascade = None
    cascade = None if cascade is None or cascade.empty() else cascade
    panels, used = [], Counter()
    for k, f in enumerate(frames(job / "source.mp4", work)):
        img = trim(cv2.imread(str(f)))
        h, w = img.shape[:2]
        if w < 200 or h < 200:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = list(cascade.detectMultiScale(cv2.equalizeHist(gray), scaleFactor=1.1, minNeighbors=5,
                                              minSize=(max(40, w // 12),) * 2)) if cascade is not None else []
        face = max(faces, key=lambda b: b[2] * b[3]) if faces else None
        target = ((face[0] + face[2] / 2) / w, (face[1] + face[3] / 2) / h) if face is not None else saliency_peak(img)
        metrics, used_by = look(img, smart)
        used[used_by] += 1
        ratio = h / w
        bubbles = min(100.0, len(texts[k]) / 1.6) if k in texts else min(100.0, 250.0 * float((gray > 245).mean()))
        edge = min(target[0], 1 - target[0])  # a subject at the very edge crops badly
        panels.append({"panel": k, "second": k * PANEL_SECONDS + 1, "w": w, "h": h, "look": metrics,
                       "faces": len(faces), "target": [round(target[0], 3), round(target[1], 3)],
                       "aim": "face" if face is not None else "saliency",
                       "signals": {"face": min(100.0, 400.0 * face[2] * face[3] / (w * h)) if face is not None else 0.0,
                                   "story": story.get(k, 0.0),
                                   "frame": (40.0 if ratio < 0.35 else 100.0 if ratio <= TALL else 75.0) * (0.6 + 0.4 * min(1.0, edge * 4)),
                                   "words": bubbles},
                       "line": line.get(k, ""), "hash": dhash(img)})
    for key in LOOK:  # each look metric 0-100 across the part, as smart-thumbnailer normalises
        top = max((p["look"][key] for p in panels), default=1.0) or 1.0
        for p in panels:
            p["look"][key] = round(100.0 * max(0.0, p["look"][key]) / top, 1)
    for p in panels:
        p["signals"]["visual"] = sum(LOOK[k] * p["look"][k] for k in LOOK) / sum(LOOK.values())
        p["score"] = round(sum(WEIGHTS[k] * p["signals"][k] for k in WEIGHTS) / 0.8, 1)  # 0-100 before variety
        p["signals"] = {k: round(v, 1) for k, v in p["signals"].items()}
    ranked, seen = [], []
    for p in sorted(panels, key=lambda p: -p["score"]):
        p["near_copy"] = not all(bin(p["hash"] ^ h).count("1") >= DHASH_MIN for h in seen)
        if not p["near_copy"]:
            seen.append(p["hash"])
        ranked.append(p)
    tools = {"metrics": used.most_common(1)[0][0] if used else "none",
             "faces": "lbpcascade_animeface" if cascade is not None else "none (saliency aim)",
             "words": "OCR" if texts else "white share"}
    return ranked, tools


def why(p: dict, role: str) -> str:
    s = p["signals"]
    top = sorted(("visual", "face", "story", "frame"), key=lambda k: -s[k])[:2]
    return f"{role}: panel {p['panel']} score {p['score']} (best at {top[0]} {s[top[0]]:.0f}, {top[1]} {s[top[1]]:.0f}; words -{s['words']:.0f})"


def best_set(pool: list[dict], n: int, ordered: bool, need=None) -> list[dict]:
    """The n panels that work together best: mean score plus the variety bonus, not just the n top scores."""
    best, value = pool[:n], -1.0
    for combo in itertools.combinations(pool, n):
        if need and not need(combo):
            continue
        variety = min(apart(a, b) for a, b in itertools.combinations(combo, 2))
        v = sum(p["score"] for p in combo) / n * (1 - VARIETY) + 100 * VARIETY * variety
        if v > value:
            best, value = list(combo), v
    return sorted(best, key=lambda p: p["panel"]) if ordered else best


def label(text: str, to: list[float]) -> dict:
    """A label across from its target, never top-right (the badge lives there)."""
    to = [round(min(0.95, max(0.05, to[0])), 3), round(min(0.92, max(0.08, to[1])), 3)]
    x, y = (0.2 if to[0] > 0.5 else 0.78), (0.14 if to[1] > 0.4 else 0.86)
    return {"text": text, "at": [x, 0.86 if x > 0.6 and y < 0.3 else y], "to": to}


def build_spec(job: Path, ranked: list[dict], segs: list[dict]) -> tuple[dict, dict]:
    hero = hero_name(segs)
    pool = [p for p in ranked if not p["near_copy"]][:POOL]
    faced = [p for p in pool if p["faces"]]
    reasons, thumbs = {}, []

    best = (faced or pool)[0]
    scene = {"name": "auto-scene", "style": "scene", "panel": best["second"], "focus": [0.5, max(0.2, min(0.6, best["target"][1]))]}
    reasons["scene"] = [why(best, "main")]
    if best["h"] / best["w"] > TALL:
        sides = best_set([p for p in pool if p is not best], 2, True)
        scene.update(fit="contain", sides=[p["second"] for p in sides])
        reasons["scene"] += [why(p, "side") for p in sides]
        to = [0.5 + (best["target"][0] - 0.5) * (720 * best["w"] / best["h"]) / W, best["target"][1]]
    else:
        to = [best["target"][0], 0.5 + (best["target"][1] - scene["focus"][1]) * 0.6]
    word = starred(best["line"])
    word = word if word and len(word.split()) <= 3 else ("MC" if hero and hero in best["line"] else "")
    if word:
        scene["labels"] = [label(word, to)]
    thumbs.append(scene)

    is_mc = lambda p: bool(hero) and hero in p["line"]
    pair = best_set(pool, 2, False, need=lambda c: is_mc(c[0]) and not is_mc(c[1]))
    if not (is_mc(pair[0]) and not is_mc(pair[1])):  # the script never names the hero: the best two that differ
        pair = best_set(pool, 2, True)
    mc, foe = pair
    thumbs.append({"name": "auto-split", "style": "split", "panels": [mc["second"], foe["second"]],
                   "focus": [[0.5, max(0.2, min(0.7, p["target"][1]))] for p in pair],
                   "labels": [{"text": "MC", "at": [0.13, 0.86], "to": [round(0.05 + mc["target"][0] * 0.42, 3), 0.5]},
                              {"text": threat_label(foe["line"]), "at": [0.82, 0.86],
                               "to": [round(0.53 + foe["target"][0] * 0.42, 3), 0.5]}]})
    reasons["split"] = [why(mc, "left"), why(foe, "right")]

    trio = best_set(pool[:10], 3, True)
    big = max(trio, key=lambda p: p["signals"]["story"])
    i = trio.index(big)
    t3 = {"name": "auto-trio", "style": "trio", "panels": [p["second"] for p in trio],
          "focus": [[0.5, max(0.2, min(0.7, p["target"][1]))] for p in trio]}
    word = starred(big["line"])
    if word and len(word.split()) <= 2:
        t3["labels"] = [label(word, [(i * 390 + big["target"][0] * 500) / W, big["target"][1]])]
    thumbs.append(t3)
    reasons["trio"] = [why(p, f"strip {n + 1}") for n, p in enumerate(trio)]

    flags = []
    if best["score"] < LOW_SCORE:
        flags.append(f"best scene panel scores {best['score']} (< {LOW_SCORE})")
    if not faced:
        flags.append("no anime face found in the best panels")
    if len(pool) < 6:
        flags.append(f"only {len(pool)} distinct panels")
    confidence = round(max(0.0, min(100.0, best["score"] * 1.4 - 20 * len(flags))))
    spec = {"badge": badge(job), "auto": True, "review": bool(flags), "confidence": confidence, "thumbs": thumbs}
    return spec, {"reasons": reasons, "flags": flags}


def badge(job: Path) -> str:
    source = json.loads((job / "source.json").read_text(encoding="utf-8")) if (job / "source.json").exists() else {}
    return f"CH {source['chapters']}" if source.get("chapters") else "RECAP"


def manual_ranks(job: Path, ranked: list[dict]) -> list[dict]:
    """Where the hand-picked panels land in the automatic ranking: the calibration check."""
    path = job / "yt" / "thumb.json"
    spec = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"auto": True}
    if spec.get("auto"):
        return []
    order = {p["panel"]: n + 1 for n, p in enumerate(ranked)}
    out = []
    for t in spec["thumbs"]:
        for s in [t.get("panel"), *t.get("panels", []), *t.get("sides", [])]:
            if s is not None:
                k = int(s // PANEL_SECONDS)
                out.append({"thumb": t["name"], "panel": k, "rank": order.get(k), "of": len(ranked)})
    return out


def main() -> None:
    started = time.time()
    job = ROOT / sys.argv[1]
    meta = job / "yt"
    meta.mkdir(exist_ok=True)
    script = json.loads((job / "script.json").read_text(encoding="utf-8"))
    segs = script["segments"]
    if not (job / "source.mp4").exists():  # the cache is gone: the series cover is still a real thumbnail
        spec = {"badge": badge(job), "auto": True, "review": True, "confidence": 0,
                "thumbs": [{"name": "auto-cover", "style": "cover", "series": script.get("credit", job.name).split(" (")[0]}]}
        report = {"flags": ["no panel video (cache miss): cover only"], "seconds": round(time.time() - started, 1)}
    else:
        with tempfile.TemporaryDirectory() as tmp:
            ranked, tools = score_panels(job, Path(tmp), segs)
        spec, picks = build_spec(job, ranked, segs)
        report = {**picks, "tools": tools, "confidence": spec["confidence"], "seconds": round(time.time() - started, 1),
                  "panels": len(ranked), "with_faces": sum(1 for p in ranked if p["faces"]),
                  "manual_picks": manual_ranks(job, ranked), "weights": WEIGHTS, "variety": VARIETY,
                  "ranked": [{**p, "hash": f"{p['hash']:016x}"} for p in ranked]}
    (meta / "thumb-auto.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    (meta / "panel-ranks.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if not (meta / "thumb.json").exists():  # a new part: the automatic picks are its thumbnails
        plain = {**spec, "thumbs": [{**t, "name": t["name"].removeprefix("auto-")} for t in spec["thumbs"]]}
        (meta / "thumb.json").write_text(json.dumps(plain, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "ranked"}, indent=1))
    if spec["review"]:
        print("::warning::low confidence: pick the thumbnail panels by hand (" + "; ".join(report["flags"]) + ")")


if __name__ == "__main__":
    main()
