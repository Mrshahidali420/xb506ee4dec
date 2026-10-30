"""Fetch a job's source on the build machine, from <job>/source.json.

{"type": "youtube", "url": "..."}                      film from YouTube (YT_COOKIES secret helps)
{"type": "anime", "title": "death note", "episode": 1, "lang": "dub"}   via anipy-cli
{"type": "webtoon", "urls": ["...episode-1...", "..."]}  manhwa strips, then panels via manhwa_panels.py
{"type": "manga", "sources": [...], "chapters": "1-6"}  first source that works (list from find_manhwa.py)
{"type": "url", "url": "..."}                           any direct video link
"""
from __future__ import annotations

import json
import os
import re
import shutil
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
    for url in src["urls"]:  # a list page gives every episode
        sh([*UV, "--with", "gallery-dl", "gallery-dl", "-D", str(job / "pages"), "--filename",
            "e{episode_no:>02}_{num:>03}.{extension}", url])
    keep = ["--blank-text"] if src.get("bubbles") == "blank" else []  # bubbles keep their words unless asked
    sh([*UV, "--with", "numpy", "--with", "pillow", "--with", "scipy", "python", "tools/manhwa_panels.py", str(job), *keep])


def manga(job: Path, src: dict) -> None:
    """Chapters from the first source that works ("sources" from find_manhwa.py, best first).
    "chapters": "1-6" picks the range. A source that fails or gives no pages hands over to the next."""
    a, b = (src.get("chapters", "1-5").split("-") + [None])[:2]
    pages = job / "pages"
    for url in src["sources"]:
        webtoon_site = "webtoons.com" in url
        num = "episode_no" if webtoon_site else "chapter"
        name = "e{episode_no:>03}_{num:>03}.{extension}" if webtoon_site else "e{chapter:>03}{chapter_minor}_{page:>03}.{extension}"
        try:
            sh([*UV, "--with", "gallery-dl", "gallery-dl", "-D", str(pages), "--filename", name,
                "--chapter-filter", f"{a} <= {num} <= {b or a}" + ("" if webtoon_site else " and lang == 'en'"), url])
        except subprocess.CalledProcessError as exc:
            print(f"source failed, trying the next: {url} ({exc})", flush=True)
        got = {p.name.split("_")[0] for p in pages.glob("e*_*")} if pages.exists() else set()
        if len(got) >= int(b or a) - int(a) + 1:
            print(f"pages from {url}: {len(got)} chapters", flush=True)
            break
        shutil.rmtree(pages, ignore_errors=True)  # a half download would mix two scans
    else:
        sys.exit("no source gave every chapter")
    keep = ["--blank-text"] if src.get("bubbles") == "blank" else []
    sh([*UV, "--with", "numpy", "--with", "pillow", "--with", "scipy", "python", "tools/manhwa_panels.py", str(job), *keep])


def srt_to_text(srt: Path, out: Path) -> None:
    """'<second> <line>' per subtitle, the same shape as a Whisper transcript."""
    lines = []
    for block in re.split(r"\n\s*\n", srt.read_text(encoding="utf-8-sig", errors="replace")):
        rows = [r.strip() for r in block.strip().splitlines()]
        stamp = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if stamp is None:
            continue
        h, m, s, ms = map(int, re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", rows[stamp]).groups())
        text = re.sub(r"<[^>]+>", "", " ".join(r for r in rows[stamp + 1:] if r))
        lines.append(f"{h * 3600 + m * 60 + s + ms / 1000:7.1f} {text}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def kisskh(job: Path, src: dict) -> None:
    """A film or drama episode from kisskh (needs the WARP step on GitHub), plus English subtitles."""
    ep = str(src.get("episode", 1))
    sh(["kisskh", "dl", src["url"], "-f", ep, "-l", ep, "-q", src.get("quality", "720p"), "-s", "en",
        "-o", str(job / "dl")])
    next((job / "dl").rglob("*.mp4")).rename(job / "source.mp4")
    srt = next((job / "dl").rglob("*.srt"), None)
    (job / "work").mkdir(exist_ok=True)
    if srt:
        srt_to_text(srt, job / "work" / "english.txt")


def film_sheets(job: Path) -> None:
    """A labelled frame every 30 s, 30 to a sheet, so the script writer can see the scenes."""
    frames = job / "work" / "sheetframes"
    frames.mkdir(parents=True, exist_ok=True)
    sh(["ffmpeg", "-v", "error", "-i", str(job / "source.mp4"), "-vf",
        "fps=1/30,scale=320:-2,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        "text='%{pts\\:hms}':x=6:y=6:fontsize=20:fontcolor=yellow:box=1:boxcolor=black@0.6",
        str(frames / "f%04d.jpg")])
    sh(["ffmpeg", "-v", "error", "-i", str(frames / "f%04d.jpg"), "-vf", "tile=6x5",
        str(job / "work" / "film_sheet_%02d.jpg")])


def shot_sheets(job: Path) -> None:
    """One labelled frame from the middle of every camera shot, 48 to a sheet, for exact clip picking."""
    frames = job / "work" / "shotframes"
    frames.mkdir(parents=True, exist_ok=True)
    shots = [tuple(map(float, l.split())) for l in (job / "work" / "shots.txt").read_text().splitlines()]
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    for k, (a, b) in enumerate(s for s in shots if s[1] - s[0] >= 0.8):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{(a + b) / 2:.2f}", "-i", str(job / "source.mp4"),
                        "-frames:v", "1", "-vf", f"scale=240:-2,drawtext=fontfile={font}:text='{a:.1f}-{b:.1f}':"
                        "x=4:y=4:fontsize=16:fontcolor=yellow:box=1:boxcolor=black@0.7",
                        str(frames / f"f{k:05d}.jpg")], check=True)
    sh(["ffmpeg", "-v", "error", "-y", "-i", str(frames / "f%05d.jpg"), "-vf", "tile=8x6",
        str(job / "work" / "shot_sheet_%02d.jpg")])


def main() -> None:
    job = Path(sys.argv[1])
    src = json.loads((job / "source.json").read_text(encoding="utf-8"))
    if (job / "source.mp4").exists():  # restored from the Actions cache
        print("source cached, no download", flush=True)
    elif src["type"] == "url":
        sh([*UV, "--with", "yt-dlp", "yt-dlp", "-q", "-o", str(job / "source.mp4"), src["url"]])
    else:
        {"youtube": youtube, "anime": anime, "webtoon": webtoon, "manga": manga, "kisskh": kisskh}[src["type"]](job, src)
    if not (job / "work" / "shots.txt").exists():  # camera cuts, so clips never jump across a cut
        (job / "work").mkdir(exist_ok=True)
        log = subprocess.run(["ffmpeg", "-i", str(job / "source.mp4"), "-an", "-vf",
                              "scale=240:-2,select='gt(scene,0.12)',showinfo", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        cuts = [0.0] + [float(x.split(":")[1]) for x in log.split() if x.startswith("pts_time:")]
        (job / "work" / "shots.txt").write_text(
            "\n".join(f"{a:.2f} {b:.2f}" for a, b in zip(cuts, cuts[1:]) if b - a > 0.3), encoding="utf-8")
    if src["type"] not in ("webtoon", "manga") and not any(job.glob("script*.json")):  # prep run: help write the script
        film_sheets(job)
    if src.get("shot_sheets") and not (job / "work" / "shot_sheet_01.jpg").exists():
        shot_sheets(job)


if __name__ == "__main__":
    main()
