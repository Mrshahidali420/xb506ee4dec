"""Cut the clips a writer picked from an episode: any length, original sound, 9:16 with a blurred backdrop.

Usage: python tools/cut_clip.py <job> [clips file]      (needs <job>/source.mp4, so it runs on the build machine)
Reads <job>/clips.json:
  [{"start": 312.0, "end": 371.5, "title": "...", "caption": "...", "comment": "..."}]
Each end is moved to the nearest camera cut (work/shots.txt) within 2 s, so a clip never opens or stops mid-shot.
A clip may also carry "short_start"/"short_end" (inside the clip): the best part, at most 75 s, for Reels.
<job>/clip.json {"pages": ["whereanime", "manhwaindex"]} puts each Page's logo on the top band, one copy per Page.
Writes <job>/<page>_clip_NN.mp4 (full), <job>/<page>_short_NN.mp4 (Reels size) and <job>/clips_out.json (the same list with the final times).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SHORT_MAX = 60.0   # Reels stay at or under 60 s (Shahid, 11 Nov 2026)
LOGO = 150         # logo width in pixels
BRANDS = Path(__file__).resolve().parent.parent / "brand" / "pages"
REACH = 2.0  # seconds an end may move to reach a camera cut
VERTICAL = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:5[bg];"
            "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p")   # square pixels: the source's odd SAR made a 1920:3413 frame


def graph(logo: bool) -> str:
    """Blurred 9:16 frame; with a logo, a second input is laid on the top band."""
    if not logo:
        return VERTICAL
    return VERTICAL.replace(",format=yuv420p", "[v];[1:v]scale=%d:-1[l];[v][l]overlay=40:60,format=yuv420p" % LOGO)


def encode(src: Path, a: float, b: float, dest: Path, logo: Path | None) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.2f}", "-t", f"{b - a:.2f}", "-i", str(src)]
    if logo:
        cmd += ["-i", str(logo)]
    cmd += ["-filter_complex", graph(bool(logo)), "-r", "30", "-g", "60", "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(dest)]
    subprocess.run(cmd, check=True)


def short_of(clip: dict, a: float, b: float) -> tuple[float, float]:
    s, e = clip.get("short_start"), clip.get("short_end")
    if s is not None and e is not None and a <= float(s) < float(e) <= b and float(e) - float(s) <= SHORT_MAX:
        return float(s), float(e)
    return a, min(b, a + SHORT_MAX)


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
    clips = json.loads((job / (sys.argv[2] if len(sys.argv) > 2 else "clips.json")).read_text(encoding="utf-8"))
    cuts, done = cuts_of(job), []
    meta = json.loads((job / "clip.json").read_text(encoding="utf-8")) if (job / "clip.json").exists() else {}
    pages = meta.get("pages") or [meta.get("page", "")]
    for i, clip in enumerate(clips, 1):
        a, b = snapped(clip, cuts)
        sa, sb = short_of(clip, a, b)
        files = {}
        for page in pages:  # one branded copy per Page
            logo = BRANDS / f"{page}.png"
            if not logo.exists():
                sys.exit(f"missing Page logo {logo}: a clip without branding must not go out")
            full, short = f"{page}_clip_{i:02d}.mp4", f"{page}_short_{i:02d}.mp4"
            encode(job / "source.mp4", a, b, job / full, logo)
            encode(job / "source.mp4", sa, sb, job / short, logo)
            files[page] = {"full": full, "short": short}
        done.append({**clip, "start": round(a, 2), "end": round(b, 2), "short_start": round(sa, 2), "short_end": round(sb, 2), "files": files})
        print(f"clip {i}: {a:.1f}-{b:.1f}s ({b - a:.0f} s), short {sb - sa:.0f} s, pages {', '.join(pages)}", flush=True)
    (job / "clips_out.json").write_text(json.dumps(done, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
