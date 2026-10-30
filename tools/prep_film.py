"""Get a film ready for script writing, in one go.

Usage: python prep_film.py <job_dir> <youtube_url>
Writes into <job_dir>:
  source.mp4          the film, 720p at most
  work/english.txt    "<second> <english line>" per spoken segment (Groq Whisper translate)
  work/shots.txt      camera shots "<start> <end>", so clips never cross a cut
Steps that already have their output are skipped, so a failed run can be restarted.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_short import FFMPEG, env_key  # noqa: E402


def download(job: Path, url: str) -> None:
    if (job / "source.mp4").exists():
        return
    subprocess.run(["yt-dlp", "-q", "--no-playlist", "-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
                    "--merge-output-format", "mp4", "-o", str(job / "source.mp4"), url], check=True)


def translate(job: Path) -> None:
    out = job / "work" / "english.txt"
    if out.exists():
        return
    audio = job / "work" / "audio.mp3"  # mono 24 kbps keeps a 2-hour film under Groq's 25 MB limit
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(job / "source.mp4"), "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", "24k", str(audio)], check=True)
    from groq import Groq

    with audio.open("rb") as fh:
        res = Groq(api_key=env_key("GROQ_API_KEY")).audio.translations.create(
            file=(audio.name, fh.read()), model="whisper-large-v3", response_format="verbose_json")
    segs = res.model_dump().get("segments") or []
    (job / "work" / "english.json").write_text(json.dumps(segs, ensure_ascii=False), encoding="utf-8")
    out.write_text("".join(f"{s['start']:7.1f} {s['text'].strip()}\n" for s in segs), encoding="utf-8")


def shots(job: Path) -> None:
    out = job / "work" / "shots.txt"
    if out.exists():
        return
    log = subprocess.run([FFMPEG, "-i", str(job / "source.mp4"), "-an", "-vf",
                          "scale=240:-2,select='gt(scene,0.12)',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    cuts = [0.0] + [float(x.split(":")[1]) for x in log.split() if x.startswith("pts_time:")]
    out.write_text("\n".join(f"{a:.2f} {b:.2f}" for a, b in zip(cuts, cuts[1:]) if b - a > 0.3), encoding="utf-8")


def main() -> None:
    job, url = Path(sys.argv[1]), sys.argv[2]
    (job / "work").mkdir(parents=True, exist_ok=True)
    download(job, url)
    print("downloaded", flush=True)
    translate(job)
    print("translated", flush=True)
    shots(job)
    print("shots", len((job / "work" / "shots.txt").read_text().splitlines()), flush=True)


if __name__ == "__main__":
    main()
