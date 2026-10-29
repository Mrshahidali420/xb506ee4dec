"""Fetch a job's source on the build machine, from <job>/source.json.

{"type": "youtube", "url": "..."}                      film from YouTube (YT_COOKIES secret helps)
{"type": "anime", "title": "death note", "episode": 1, "lang": "dub"}   via anipy-cli
{"type": "webtoon", "urls": ["...episode-1...", "..."]}  manhwa strips, then panels via manhwa_panels.py
{"type": "url", "url": "..."}                           any direct video link
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

UV = ["uv", "run", "--python", "3.11"]


def sh(args: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(args[:8]), flush=True)
    subprocess.run(args, cwd=cwd, check=True)


def youtube(job: Path, src: dict) -> None:
    cookies = []
    if os.environ.get("YT_COOKIES"):
        Path("/tmp/cookies.txt").write_text(os.environ["YT_COOKIES"], encoding="utf-8")
        cookies = ["--cookies", "/tmp/cookies.txt"]
    sh([*UV, "--with", "yt-dlp", "yt-dlp", "-q", *cookies, "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
        "--merge-output-format", "mp4", "-o", str(job / "source.mp4"), src["url"]])


def anime(job: Path, src: dict) -> None:
    code = f"""
from anipy_api.provider import get_provider, LanguageTypeEnum as L
from anipy_api.anime import Anime
p = get_provider('animehub')
r = [x for x in p.get_search({src['title']!r}) if x.name.strip().lower() == {src['title']!r}.lower()] or p.get_search({src['title']!r})
a = Anime.from_search_result(p, r[0])
lang = L.DUB if {src.get('lang', 'dub')!r} == 'dub' and L.DUB in a.languages else L.SUB
s = a.get_video(a.get_episodes(lang=lang)[{int(src['episode']) - 1}], lang=lang, preferred_quality=720)
print('URL=' + str(s.url)); print('REF=' + (s.referrer or ''))
"""
    out = subprocess.run([*UV, "--with", "anipy-cli", "python", "-c", code], capture_output=True, text=True, check=True)
    lines = out.stdout.splitlines()
    url = next(l[4:] for l in lines if l.startswith("URL="))
    ref = next((l[4:] for l in lines if l.startswith("REF=")), "")
    sh([*UV, "--with", "yt-dlp", "yt-dlp", "-q", *(["--referer", ref] if ref else []), "--remux-video", "mp4",
        "-o", str(job / "source.mp4"), url])


def webtoon(job: Path, src: dict) -> None:
    for i, url in enumerate(src["urls"], 1):
        sh([*UV, "--with", "gallery-dl", "gallery-dl", "-D", str(job / "pages"), "--filename",
            f"e{i}_{{num:>03}}.{{extension}}", url])
    sh([*UV, "--with", "numpy", "--with", "pillow", "python", "tools/manhwa_panels.py", str(job)])


def main() -> None:
    job = Path(sys.argv[1])
    src = json.loads((job / "source.json").read_text(encoding="utf-8"))
    if src["type"] == "url":
        sh([*UV, "--with", "yt-dlp", "yt-dlp", "-q", "-o", str(job / "source.mp4"), src["url"]])
    else:
        {"youtube": youtube, "anime": anime, "webtoon": webtoon}[src["type"]](job, src)
    if not (job / "work" / "shots.txt").exists():  # camera cuts, so clips never jump across a cut
        (job / "work").mkdir(exist_ok=True)
        log = subprocess.run(["ffmpeg", "-i", str(job / "source.mp4"), "-an", "-vf",
                              "scale=240:-2,select='gt(scene,0.12)',showinfo", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        cuts = [0.0] + [float(x.split(":")[1]) for x in log.split() if x.startswith("pts_time:")]
        (job / "work" / "shots.txt").write_text(
            "\n".join(f"{a:.2f} {b:.2f}" for a, b in zip(cuts, cuts[1:]) if b - a > 0.3), encoding="utf-8")


if __name__ == "__main__":
    main()
