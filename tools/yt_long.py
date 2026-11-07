"""Make a part's 16:9 YouTube script from its Facebook script (same lines and clips), by code.

Usage: python yt_long.py <part job>

Writes <part>/script-youtube.json. Build it with
  gh workflow run build.yml -f job=<part> -f script=script-youtube.json
then upload with youtube.yml (meta=<part>/yt). The Short comes after, linked to this video.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
YOUTUBE = {  # what the Swordmaster 16:9 parts used (jobs/manhwa-swordmaster-son-p1/script-youtube.json)
    "aspect": "16:9",
    "output": "final_youtube.mp4",
    "voice_chain": ["kokoro", "edge"],
    "kokoro_voice": "am_liam",
    "music_db_under_voice": 12,
}


def main() -> None:
    part = ROOT / sys.argv[1]
    src = json.loads((part / "script.json").read_text(encoding="utf-8"))
    out = {k: v for k, v in src.items() if k not in ("caption", "comment")} | YOUTUBE
    (part / "script-youtube.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{part.name}: script-youtube.json, {len(out['segments'])} lines")


if __name__ == "__main__":
    main()
