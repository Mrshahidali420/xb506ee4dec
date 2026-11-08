"""Make a promo / YouTube Short script from a full video's script, with no writer (saves tokens).

Usage: python auto_promo.py <parent job> <promo job> [--yt <long video link>] [--climax [--hook <line index>]]

It keeps the hook (line 1), then the run of lines with the most *keywords* in a row,
reusing the parent's clips (same source, so the same frames), and ends pointing to the full video.
With --yt it writes script-ytshort.json + yt/ for youtube.yml; without it, script.json for Facebook.
--climax makes a second Short from the part's ending, opened by its most starred line, so one part gives two Shorts.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIZE = 13  # lines after the hook: about 60 s with the hook and the end line


def best_run(segs: list[dict]) -> list[dict]:
    """The SIZE lines in a row (after the opening 2) with the most starred keywords: the busiest scene."""
    body = segs[2:-1] or segs[1:]
    score = lambda run: sum(s["say"].count("*") for s in run)
    starts = range(max(len(body) - SIZE, 0) + 1)
    pick = max(starts, key=lambda i: score(body[i:i + SIZE]))
    return body[pick:pick + SIZE]


def main() -> None:
    args = sys.argv[1:]
    yt = args[args.index("--yt") + 1] if "--yt" in args else ""
    parent, promo = ROOT / args[0], ROOT / args[1]
    src = json.loads((parent / "script.json").read_text(encoding="utf-8"))
    promo.mkdir(parents=True, exist_ok=True)
    shutil.copy(parent / "source.json", promo / "source.json")
    where = "our channel" if yt else "our page"
    if "--climax" in args:  # the ending, opened by its strongest line (most starred words) as the hook
        tail = src["segments"][-SIZE - 2:-1]
        hook = src["segments"][int(args[args.index("--hook") + 1])] if "--hook" in args else             max(reversed(tail), key=lambda seg: seg["say"].count("*"))  # ties: the later, bigger moment
        tail = tail if hook in tail else tail[1:]
        run = [hook, *[seg for seg in tail if seg is not hook]]
    else:
        run = [src["segments"][0], *best_run(src["segments"])]
    end = {"say": f"The full story is on {where}.", "clips": run[-1]["clips"]}
    segs = [*run, end]
    out = {k: v for k, v in src.items() if k not in ("segments", "caption", "comment", "title")}
    out["title"] = segs[0]["say"].replace("*", "").rstrip(".") + " . . . (highlight)"
    out["segments"] = segs
    if yt:
        out["output"] = "final_ytshort.mp4"
        (promo / "script-ytshort.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        meta = promo / "yt"
        meta.mkdir(exist_ok=True)
        hook = segs[0]["say"].replace("*", "").rstrip(".")
        (meta / "title.txt").write_text(f"{hook[:88]} #Shorts", encoding="utf-8")
        (meta / "description.txt").write_text(f"Full story: {yt}\n\n#Shorts #manhwa #manhwarecap\n", encoding="utf-8")
        (meta / "tags.txt").write_text("manhwa recap,manhwa,webtoon recap,shorts", encoding="utf-8")
        for name in ("playlists.txt",):  # same series playlist as the parent's YouTube upload, if it has one
            given = parent / "yt" / name
            if given.exists():
                shutil.copy(given, meta / name)
    else:
        out["caption"] = f"{src['segments'][0]['say'].replace('*', '')} Full recap is on our page 👉 link in the comments"
        (promo / "script.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{promo.name}: {len(segs)} lines")


if __name__ == "__main__":
    main()
