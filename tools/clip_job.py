"""Start a clip job for one trending episode: prep on GitHub, sheets come down, a writer picks the clips.

python tools/clip_job.py prep "<show title>" <episode> [--lang sub|dub] [--page whereanime|manhwaindex]
    makes jobs/clip-<show>-e<NN>/ (source.json + meta.json), runs the prep build on GitHub (same as a recap prep:
    download, subtitles or translated speech, camera cuts, sheets) and downloads only the small sheets.
python tools/clip_job.py cut <job>
    pushes <job>/clips.json, runs clip.yml, waits, downloads the clips into <job>/out/ for you to watch.
The writer brief is daily/CLIPWRITER.md. Nothing here posts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python -I does not add the script folder
from daily import ROOT, dispatch, push, save, sh, slug, wait  # noqa: E402


def prep(args: argparse.Namespace) -> None:
    job = f"jobs/clip-{slug(args.title)}-e{args.episode:02d}"
    folder = ROOT / job
    folder.mkdir(parents=True, exist_ok=True)
    save(folder / "source.json", {"type": "anime", "title": args.title.lower(), "episode": args.episode, "lang": args.lang})
    save(folder / "meta.json", {"kind": "clip", "page": args.page, "title": args.title, "episode": args.episode})
    push(f"Clip job {job}")
    run = dispatch(job)
    print(f"prep run {run}", flush=True)
    result = wait({run: job}).get(run, "timeout")
    if result != "success":
        raise SystemExit(f"prep failed: {result} (run {run})")
    sheets = folder / "sheets"
    shutil.rmtree(sheets, ignore_errors=True)
    sh("gh", "run", "download", run, "-n", "sheets", "-D", str(sheets))
    print(f"ready: {job}/sheets  (now follow daily/CLIPWRITER.md)")


def cut(args: argparse.Namespace) -> None:
    job = args.job.rstrip("/")
    push(f"Clips for {job}")
    sh("gh", "workflow", "run", "clip.yml", "-f", f"job={job}")
    run = sh("gh", "run", "list", "--workflow", "clip.yml", "--limit", "1", "--json", "databaseId", "--jq", ".[0].databaseId")
    result = wait({run: job}).get(run, "timeout")
    if result != "success":
        raise SystemExit(f"cut failed: {result} (run {run})")
    out = ROOT / job / "out"
    shutil.rmtree(out, ignore_errors=True)
    sh("gh", "run", "download", run, "-n", "clips", "-D", str(out))
    print(f"clips in {out}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prep")
    a.add_argument("title")
    a.add_argument("episode", type=int)
    a.add_argument("--lang", default="sub", choices=["sub", "dub"])
    a.add_argument("--page", default="whereanime")
    c = sub.add_parser("cut")
    c.add_argument("job")
    args = p.parse_args()
    (prep if args.cmd == "prep" else cut)(args)


if __name__ == "__main__":
    main()
