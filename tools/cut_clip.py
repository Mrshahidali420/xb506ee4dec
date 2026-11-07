"""Cut the clips a writer picked from an episode: any length, original sound, 9:16 with a blurred backdrop.

Usage: python tools/cut_clip.py <job>      (needs <job>/source.mp4, so it runs on the build machine)
Reads <job>/clips.json:
  [{"start": 312.0, "end": 371.5, "title": "...", "caption": "...", "comment": "..."}]
Each end is moved to the nearest camera cut (work/shots.txt) within 2 s, so a clip never opens or stops mid-shot.
Writes <job>/clip_NN.mp4 and <job>/clips_out.json (the same list with the final times).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REACH = 2.0  # seconds an end may move to reach a camera cut
VERTICAL = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
            "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p")


def cuts_of(job: Path) -> list[float]:
    f = job / "work" / "shots.txt"
    if not f.exists():
        return []
    return sorted({float(x) for line in f.read_text().splitlines() for x in line.split()})


def snapped(clip: dict, cuts: list[float]) -> tuple[float, float]:
    def near(t: float) -> float:
        c = [x for x in cuts if abs(x - t) <= REACH]
        return min(c, key=lambda x: abs(x - t)) if c else t

    a, b = near(float(clip["start"])), near(float(clip["end"]))
    return (a, b) if b - a >= 5 else (float(clip["start"]), float(clip["end"]))


def main() -> None:
    job = Path(sys.argv[1])
    clips = json.loads((job / "clips.json").read_text(encoding="utf-8"))
    cuts, done = cuts_of(job), []
    for i, clip in enumerate(clips, 1):
        a, b = snapped(clip, cuts)
        dest = job / f"clip_{i:02d}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(job / "source.mp4"),
                        "-filter_complex", VERTICAL, "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
                        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)], check=True)
        done.append({**clip, "start": round(a, 2), "end": round(b, 2), "file": dest.name})
        print(f"clip {i}: {a:.1f}-{b:.1f}s ({b - a:.0f} s) -> {dest.name}", flush=True)
    (job / "clips_out.json").write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
