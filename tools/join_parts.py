"""Join finished part videos into one and fill {chapters} in the description.

python tools/join_parts.py <meta-folder> <out-folder> <part1.mp4> <part2.mp4> ...
meta folder: description.txt (with {chapters}) and parts.txt (one short name per part, same order).
Writes <out>/joined.mp4 and <out>/description.txt. Parts are joined with -c copy when their
streams match, otherwise re-encoded.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,r_frame_rate,sample_rate,channels:format=duration",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def stamp(seconds: float) -> str:
    s = int(round(seconds))
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main() -> None:
    meta, out, parts = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]
    parts_file = meta / "parts.txt"  # absent for a single video (a Short): no chapters
    names = [n.strip() for n in parts_file.read_text(encoding="utf-8").splitlines() if n.strip()] if parts_file.exists() else []
    if parts_file.exists() and len(names) != len(parts) or not parts_file.exists() and len(parts) != 1:
        sys.exit(f"{len(parts)} videos but {len(names)} names in parts.txt")
    out.mkdir(parents=True, exist_ok=True)
    infos = [probe(p) for p in parts]
    durations = [float(i["format"]["duration"]) for i in infos]
    sigs = [json.dumps(i["streams"], sort_keys=True) for i in infos]
    lst = out / "parts.list"
    lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts), encoding="utf-8")
    joined = out / "joined.mp4"
    base = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if len(set(sigs)) == 1:
        subprocess.run(base + ["-c", "copy", "-movflags", "+faststart", str(joined)], check=True)
    else:
        print("streams differ, re-encoding")
        subprocess.run(base + ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-r", "30", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart",
                               str(joined)], check=True)
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d
    chapters = "\n".join(f"{stamp(s)} Part {i + 1}: {n}" for i, (s, n) in enumerate(zip(starts, names)))
    desc = (meta / "description.txt").read_text(encoding="utf-8").replace("{chapters}", chapters)
    (out / "description.txt").write_text(desc, encoding="utf-8")
    print(chapters)
    print(f"joined {stamp(sum(durations))} -> {joined}")


if __name__ == "__main__":
    main()
