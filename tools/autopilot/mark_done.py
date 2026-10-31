"""Mark an autopilot job as queued: its series moves on to the next 5 chapters.

Usage: python mark_done.py jobs/auto-<slug>-p<N>
Reads the job's meta.json for title and part, so running it twice for one job does not advance twice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERIES = ROOT / "autopilot" / "series.json"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    meta = json.loads((ROOT / sys.argv[1] / "meta.json").read_text(encoding="utf-8"))
    series = json.loads(SERIES.read_text(encoding="utf-8"))
    last = int(meta["chapters"].split("-")[-1])
    for slug, entry in series.items():
        if entry["title"] == meta["title"] and entry["part"] == meta["part"]:
            entry.update(next_chapter=last + 1, part=entry["part"] + 1, attempts=0)
            SERIES.write_text(json.dumps(series, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{slug}: next is part {entry['part']}, from chapter {entry['next_chapter']}")
            return
    print("series already moved on, nothing to do")


if __name__ == "__main__":
    main()
