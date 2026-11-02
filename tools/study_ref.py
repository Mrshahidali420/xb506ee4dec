"""Study a reference recap video: download it small, then pull out everything a writer can learn from.

Usage: python study_ref.py <url> [name]      (Facebook, YouTube, TikTok, any yt-dlp link)
Run:   uv run -q --python 3.11 --with yt-dlp --with shazamio --with pillow --with requests --with truststore python tools/study_ref.py <url> [name]

Writes refs/<name>/ (git-ignored): video.mp4 (<=480p), audio.mp3, sheets/sheet_NN.jpg (a frame every 3 s,
24 tiles per sheet, time stamp on each tile), transcript.txt (Groq Whisper, time stamped), music.txt
(Shazam, 10 s windows every 10 s), meta.json (title, uploader, duration, words per minute, cut rate).
Then follow REF-STUDY.md. Every step skips if its output already exists, so a re-run only fills gaps.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests
import truststore
from PIL import Image, ImageDraw, ImageFont

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent.parent
FRAME_EVERY = 3      # seconds between sheet frames
TILES = 24           # frames per sheet
COLS = 6
TILE_W = 270         # tile width in px; height follows the video
SCENE_CUT = 0.3      # ffmpeg scene-change threshold


def find_ffmpeg() -> None:
    """Put ffmpeg on PATH; on this PC it lives only on the Windows user PATH."""
    if shutil.which("ffmpeg"):
        return
    out = subprocess.run(["powershell", "-c", "[Environment]::GetEnvironmentVariable('Path','User')"],
                         capture_output=True, text=True).stdout
    for p in out.strip().split(";"):
        if "ffmpeg" in p.lower() and Path(p.strip(), "ffmpeg.exe").exists():
            os.environ["PATH"] = p.strip() + os.pathsep + os.environ["PATH"]
            return
    sys.exit("ffmpeg not found")


def run(args: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(a) for a in args[:7]), flush=True)
    return subprocess.run(args, check=True, **kw)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "ref"


def download(url: str, d: Path) -> dict:
    """Low-res video plus yt-dlp's info (title, uploader, duration)."""
    info_file = d / "info.json"
    if not (d / "video.mp4").exists():
        run(["uvx", "-q", "yt-dlp", "-f", "bv*[height<=480]+ba/b[height<=480]/worst", "--merge-output-format", "mp4",
             "--write-info-json", "-o", str(d / "video.%(ext)s"), url])
        found = list(d.glob("video.info.json"))
        if found:
            found[0].replace(info_file)
    return json.loads(info_file.read_text(encoding="utf-8")) if info_file.exists() else {}


def duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def make_audio(video: Path, mp3: Path) -> None:
    if not mp3.exists():
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(mp3)])


def stamp(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def make_sheets(video: Path, d: Path) -> int:
    """One frame every 3 s, 24 per sheet, with the time on each tile."""
    sheets = d / "sheets"
    if sheets.exists() and list(sheets.glob("sheet_*.jpg")):
        return len(list(sheets.glob("sheet_*.jpg")))
    sheets.mkdir(exist_ok=True)
    tmp = d / "_frames"
    tmp.mkdir(exist_ok=True)
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", f"fps=1/{FRAME_EVERY},scale={TILE_W}:-2",
         str(tmp / "f_%04d.jpg")])
    frames = sorted(tmp.glob("f_*.jpg"))
    font = ImageFont.load_default(size=16)
    for n in range(0, len(frames), TILES):
        batch = frames[n:n + TILES]
        with Image.open(batch[0]) as first:
            w, h = first.size
        rows = (len(batch) + COLS - 1) // COLS
        sheet = Image.new("RGB", (w * COLS, h * rows), "black")
        for i, f in enumerate(batch):
            with Image.open(f) as im:
                tile = im.convert("RGB")
            draw = ImageDraw.Draw(tile)
            label = stamp((n + i) * FRAME_EVERY)
            draw.rectangle((0, 0, 48, 22), fill="black")
            draw.text((3, 2), label, fill="yellow", font=font)
            sheet.paste(tile, ((i % COLS) * w, (i // COLS) * h))
        sheet.save(sheets / f"sheet_{n // TILES + 1:02d}.jpg", quality=80)
    shutil.rmtree(tmp)
    return len(list(sheets.glob("sheet_*.jpg")))


def groq_key() -> str:
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    env = Path.home() / ".config" / "watch" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("GROQ_API_KEY not found (env or ~/.config/watch/.env)")


def transcribe(mp3: Path, out: Path) -> list[dict]:
    """Groq whisper-large-v3 with segment time stamps. Returns the segments."""
    cache = out.with_suffix(".json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    with mp3.open("rb") as f:
        r = requests.post("https://api.groq.com/openai/v1/audio/transcriptions",
                          headers={"Authorization": f"Bearer {groq_key()}"},
                          files={"file": (mp3.name, f)},
                          data={"model": "whisper-large-v3", "response_format": "verbose_json", "language": "en"},
                          timeout=300)
    r.raise_for_status()
    segs = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in r.json().get("segments", [])]
    cache.write_text(json.dumps(segs), encoding="utf-8")
    out.write_text("\n".join(f"[{stamp(s['start'])}] {s['text']}" for s in segs) + "\n", encoding="utf-8")
    return segs


async def shazam_windows(video: Path, d: Path, length: float) -> list[tuple[int, str]]:
    from shazamio import Shazam
    shazam = Shazam()
    hits = []
    for start in range(0, int(length), 10):
        clip = d / "_win.mp3"
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-t", "10", "-i", str(video), "-vn", str(clip)])
        try:
            track = (await shazam.recognize(str(clip))).get("track")
        except Exception as exc:  # network blip: treat the window as no hit
            print(f"shazam {start}s: {exc}", file=sys.stderr)
            track = None
        hits.append((start, f"{track['title']} | {track['subtitle']}" if track else ""))
    clip.unlink(missing_ok=True)
    return hits


def music(video: Path, d: Path, length: float) -> None:
    """music.txt: every track with the time ranges it was heard in."""
    out = d / "music.txt"
    if out.exists():
        return
    runs: dict[str, list[list[int]]] = {}
    for start, name in asyncio.run(shazam_windows(video, d, length)):
        if not name:
            continue
        spans = runs.setdefault(name, [])
        if spans and spans[-1][1] == start:  # window touches the last one: extend it
            spans[-1][1] = start + 10
        else:
            spans.append([start, start + 10])
    lines = [f"{n} | {', '.join(f'{stamp(a)}-{stamp(min(b, length))}' for a, b in s)}" for n, s in runs.items()]
    out.write_text("\n".join(lines) + "\n" if lines else "no Shazam hits (instrumental or under the voice)\n", encoding="utf-8")


def cut_rate(video: Path, length: float) -> float:
    """Scene changes per 10 s."""
    p = subprocess.run(["ffmpeg", "-i", str(video), "-vf", f"select='gt(scene,{SCENE_CUT})',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    cuts = p.stderr.count("pts_time:")
    return round(cuts / length * 10, 2)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    url = sys.argv[1]
    name = slug(sys.argv[2]) if len(sys.argv) > 2 else ""
    find_ffmpeg()
    d = ROOT / "refs" / (name or "_new")
    d.mkdir(parents=True, exist_ok=True)
    info = download(url, d)
    if not name:  # no name given: derive one from the title and rename
        name = slug(info.get("title", "")) or "ref"
        final = ROOT / "refs" / name
        if not final.exists():
            d.rename(final)
            d = final
    video = d / "video.mp4"
    length = duration(video)
    make_audio(video, d / "audio.mp3")
    sheets = make_sheets(video, d)
    segs = transcribe(d / "audio.mp3", d / "transcript.txt")
    words = sum(len(s["text"].split()) for s in segs)
    music(video, d, length)
    meta = {"url": url, "title": info.get("title"), "uploader": info.get("uploader"), "duration_s": round(length, 1),
            "words": words, "words_per_minute": round(words / length * 60), "cuts_per_10s": cut_rate(video, length),
            "frame_sheets": sheets}
    (d / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print(f"done: {d}")


if __name__ == "__main__":
    main()
