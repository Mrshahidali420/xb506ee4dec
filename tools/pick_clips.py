"""Pick the clips of an episode with no paid AI: signals find the hot stretches, Groq (free) writes the words.

Usage: python tools/pick_clips.py <job> [out.json]     (default out: clips.json; needs <job>/source.mp4, so it runs on the build machine)
Reads <job>/clip.json (title, episode) and <job>/work/ or sheets: shots.txt and english.txt.
Writes <job>/clips.json, the same list tools/cut_clip.py reads:
  [{start, end, short_start, short_end, title, caption, comment, why}]
1. Per-second score from loudness, swell, speech, bass, cuts, motion and subtitles (tools/hype).
2. Best continuous windows of 90-210 s, never in the OP or ED, ends snapped to camera cuts.
3. For each window, the best 60 s inside it becomes the Reels short.
4. Groq reads the speech of each window and writes title, caption, comment. A window that is a recap,
   a preview or credits is dropped. If Groq fails, plain words are used, so a run never stops here.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.hype import oped, select, signals  # noqa: E402

LENGTHS = (90, 150, 210)
TOP = 3
SHORT_LEN = 60
LINE_SPAN = 6.0     # seconds a subtitle line is shown at most
TEXT_CAP = 3500     # characters of speech sent to Groq per window
URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"


def lines_of(job: Path) -> list[tuple[float, str]]:
    for f in (job / "work" / "english.txt", job / "sheets" / "english.txt", job / "english.txt"):
        if f.exists():
            out = []
            for raw in f.read_text(encoding="utf-8").splitlines():
                m = re.match(r"\s*([\d.]+)\s+(.*)", raw)
                if m and m.group(2).strip():
                    out.append((float(m.group(1)), m.group(2).strip()))
            return out
    return []


def subtitle_signal(lines: list[tuple[float, str]], n: int) -> np.ndarray | None:
    if not lines:
        return None
    out = np.zeros(n)
    for i, (t, text) in enumerate(lines):
        end = min(lines[i + 1][0] if i + 1 < len(lines) else t + LINE_SPAN, t + LINE_SPAN)
        a, b = int(t), min(n, int(end) + 1)
        if b > a:
            out[a:b] += (len(text) + 4 * (text.count("!") + text.count("?"))) / (b - a)
    return out


def cuts_of(job: Path, video: Path) -> list[float]:
    f = job / "work" / "shots.txt"
    if f.exists():
        return sorted({float(x) for line in f.read_text().splitlines() for x in line.split()})
    return signals.scene_cuts(video)


def best_inside(score: np.ndarray, a: float, b: float) -> tuple[float, float]:
    """The best SHORT_LEN seconds inside [a, b]."""
    s = select.smooth(score)
    a, b = int(a), int(b)
    if b - a <= SHORT_LEN:
        return float(a), float(b)
    csum = np.concatenate([[0.0], np.cumsum(s)])
    start = max(range(a, b - SHORT_LEN + 1), key=lambda t: csum[t + SHORT_LEN] - csum[t])
    return float(start), float(start + SHORT_LEN)


def words_for(show: str, episode: int, windows: list[dict], lines: list[tuple[float, str]]) -> list[dict]:
    """Ask Groq for title, caption, comment and a keep flag per window. Plain words if it fails."""
    plain = [{"keep": True, "title": f"{show} Episode {episode}: the moment of the episode", "caption": f"{show} Episode {episode}. What a scene.",
              "comment": "What did you think of this one?", "why": "loudest, busiest stretch of the episode"} for _ in windows]
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return plain
    parts = []
    for i, w in enumerate(windows, 1):
        text = " | ".join(t for s, t in lines if w["start"] <= s < w["end"])[:TEXT_CAP]
        parts.append(f"WINDOW {i} ({int(w['start'])}-{int(w['end'])} s) speech: {text or '(no speech)'}")
    prompt = (f"You help a Facebook Page share anime clips. Show: {show}, episode {episode}.\n"
              "Below is the speech of candidate clips, in English. For each window return an object with:\n"
              '  keep (false if it is a recap of earlier episodes, a next-episode preview, credits or empty chatter),\n'
              "  title (under 90 chars, starts with the show and episode, says what happens),\n"
              "  caption (2-3 short lines, a hook, then 4-5 hashtags; no spoilers beyond this clip),\n"
              "  comment (one question for the first comment),\n  why (one short line).\n"
              'Answer ONE JSON object: {"clips": [ ... one per window, in order ... ]}.\n\n' + "\n\n".join(parts))
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.4,
                       "max_tokens": 2500, "response_format": {"type": "json_object"}}).encode()
    try:
        req = urllib.request.Request(URL, body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "clip-picker"})
        got = json.loads(json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"])["clips"]
        return [{**plain[i], **{k: v for k, v in g.items() if k in plain[i] and v not in (None, "")}} for i, g in enumerate(got[:len(windows)])] + plain[len(got):]
    except Exception as err:  # never stop the run for the words
        print(f"Groq failed ({type(err).__name__}); plain words used", flush=True)
        return plain


def main() -> None:
    job = Path(sys.argv[1])
    meta = json.loads((job / "clip.json").read_text(encoding="utf-8"))
    video = job / "source.mp4"
    length = signals.duration(video)
    n = int(length)
    lines = lines_of(job)

    wav = job / "work" / "audio.wav"
    wav.parent.mkdir(exist_ok=True)
    signals.extract_wav(video, wav)
    sig = signals.audio_signals(wav, n)
    cuts = cuts_of(job, video)
    sig["cuts"] = select.cut_rate(cuts, n)
    sig["motion"] = signals.motion(video, n)
    sig["subs"] = subtitle_signal(lines, n)

    skip = oped.exclusions(meta.get("title"), meta.get("episode"), length)
    score = select.combine(sig)
    ok = select.allowed_mask(n, skip["zones"])
    found = select.best_windows(score, ok, top=TOP, lengths=LENGTHS)
    found = sorted((select.snap(w, cuts, lo=min(LENGTHS) - 30, hi=max(LENGTHS) + 30) for w in found), key=lambda w: w["start"])
    words = words_for(str(meta["title"]).title(), int(meta["episode"]), found, lines)

    out = []
    for w, t in zip(found, words):
        if not t.get("keep", True):
            print(f"dropped {w['start']:.0f}-{w['end']:.0f}: {t.get('why')}", flush=True)
            continue
        ss, se = best_inside(score, w["start"], w["end"])
        out.append({"start": round(w["start"], 1), "end": round(w["end"], 1), "short_start": ss, "short_end": se,
                    "title": t["title"], "caption": t["caption"], "comment": t["comment"], "why": t["why"],
                    "score": round(w["value"], 3), "signals": [k for k, v in sig.items() if v is not None], "oped": skip["source"]})
    (job / (sys.argv[2] if len(sys.argv) > 2 else "clips.json")).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    for c in out:
        print(f"{c['start']:.0f}-{c['end']:.0f} s  short {c['short_start']:.0f}-{c['short_end']:.0f}  {c['title']}", flush=True)


if __name__ == "__main__":
    main()
