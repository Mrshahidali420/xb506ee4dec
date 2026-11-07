"""Find the hype moments of an episode and cut them as vertical clips. Signals only, no AI model.

  python tools/hype_detect.py analyze <video> --out out/hype [--title "black clover" --episode 12] [--subs ep.srt] [--top 6]
      writes candidates.json, scores.csv and one contact sheet per candidate (cand-N.jpg) for a quick look
  python tools/hype_detect.py cut <video> --out out/hype --pick 1,3      (or --pick auto for the best 3)
      writes clip-N.mp4, 1080x1920, original sound kept
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.hype import oped, select, signals  # noqa: E402

TILE = "3x2"
SHEET_FRAMES = 6


def analyze(args: argparse.Namespace) -> None:
    video, out = Path(args.video), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    length = signals.duration(video)
    n = int(length)

    wav = out / "audio.wav"
    signals.extract_wav(video, wav)
    sig: dict[str, np.ndarray | None] = signals.audio_signals(wav, n)
    cuts = signals.scene_cuts(video)
    sig["cuts"] = select.cut_rate(cuts, n)
    sig["motion"] = signals.motion(video, n)
    sig["subs"] = signals.subtitle_density(Path(args.subs), n) if args.subs else None

    skip = oped.exclusions(args.title, args.episode, length)
    score = select.combine(sig)
    ok = select.allowed_mask(n, skip["zones"])
    windows = [select.snap(w, cuts) for w in select.best_windows(score, ok, top=args.top)]

    for i, w in enumerate(windows, 1):
        a, b = int(w["start"]), int(w["end"])
        w["rank"] = i
        w["means"] = {k: round(float(select.normalize(v)[a:b].mean()), 2) for k, v in sig.items() if v is not None}
        sheet = out / f"cand-{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(w["start"]), "-t", str(w["end"] - w["start"]), "-i", str(video),
                        "-vf", f"fps={SHEET_FRAMES}/{w['end'] - w['start']},scale=480:-2,tile={TILE}", "-frames:v", "1", str(sheet)], check=True)
        w["sheet"] = sheet.name

    (out / "candidates.json").write_text(json.dumps({
        "video": str(video), "length": length, "exclusions": skip, "signals_used": [k for k, v in sig.items() if v is not None],
        "scene_cuts": len(cuts), "candidates": windows}, indent=1))
    with open(out / "scores.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["second", "score", *[k for k, v in sig.items() if v is not None]])
        for t in range(n):
            wr.writerow([t, round(float(score[t]), 3), *[round(float(select.normalize(v)[t]), 3) for v in sig.values() if v is not None]])
    wav.unlink()
    print(f"{len(windows)} candidates -> {out / 'candidates.json'} (exclusions: {skip['source']})")


VERTICAL = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
            "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p")


def cut(args: argparse.Namespace) -> None:
    out = Path(args.out)
    cands = json.loads((out / "candidates.json").read_text())["candidates"]
    picks = [1, 2, 3] if args.pick == "auto" else [int(x) for x in args.pick.split(",")]
    for i in picks:
        w = next(c for c in cands if c["rank"] == i)
        dest = out / f"clip-{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(w["start"]), "-t", str(w["end"] - w["start"]), "-i", args.video,
                        "-filter_complex", VERTICAL, "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)], check=True)
        print(f"clip {i}: {w['start']:.1f}-{w['end']:.1f}s -> {dest}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("video")
    a.add_argument("--out", default="out/hype")
    a.add_argument("--title")
    a.add_argument("--episode", type=int)
    a.add_argument("--subs")
    a.add_argument("--top", type=int, default=6)
    c = sub.add_parser("cut")
    c.add_argument("video")
    c.add_argument("--out", default="out/hype")
    c.add_argument("--pick", default="auto")
    args = p.parse_args()
    (analyze if args.cmd == "analyze" else cut)(args)


if __name__ == "__main__":
    main()
