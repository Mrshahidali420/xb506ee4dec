"""Storyboard check: each narration line next to the frames its clips will show, so a wrong clip is easy to spot.

Usage: python tools/storyboard.py <job> [script.json]
Writes <job>/sheets/storyboard_NN.jpg (12 lines per page). Needs no video: the frames are cut out of the
prep sheets (shot_sheet_*.jpg for films and anime, panels_sheet_*.jpg for manhwa).
A line whose picture does not match its words gets new clips; then run this again.
"""
from __future__ import annotations

import bisect
import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PER_PAGE, THUMB_H, TEXT_W = 12, 150, 760
PANEL_SECONDS, PANEL_TILES, SHOT_TILES = 3.0, 24, 48
FONT = next((f for f in ("C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
             if Path(f).exists()), None)


class Frames:
    """Finds the sheet tile that shows a given source second."""

    def __init__(self, sheets: Path):
        self.sheets = sheets
        self.panels = sorted(sheets.glob("panels_sheet_*.jpg"))
        shots = []
        if (sheets / "shots.txt").exists():
            rows = [tuple(map(float, l.split())) for l in (sheets / "shots.txt").read_text().splitlines() if l.strip()]
            shots = [s for s in rows if s[1] - s[0] >= 0.8]  # the same filter shot_sheets() used
        self.starts = [a for a, _ in shots]
        self.cache: dict[Path, Image.Image] = {}

    def sheet(self, path: Path) -> Image.Image | None:
        if path not in self.cache and path.exists():
            self.cache[path] = Image.open(path).convert("RGB")
        return self.cache.get(path)

    def tile(self, second: float) -> Image.Image | None:
        cols = 8
        if self.panels:
            k, per, name = int(second // PANEL_SECONDS), PANEL_TILES, "panels_sheet_{}.jpg"
        elif self.starts and (self.sheets / "shot_sheet_01.jpg").exists():
            k, per, name = max(bisect.bisect_right(self.starts, second) - 1, 0), SHOT_TILES, "shot_sheet_{:02d}.jpg"
        else:  # older jobs have only film sheets: one frame every 30 s, 6 x 5 per sheet (nearest frame, not exact)
            k, per, name, cols = int(second // 30), 30, "film_sheet_{:02d}.jpg", 6
        img = self.sheet(self.sheets / name.format(k // per + 1))
        if img is None:
            return None
        rows = per // cols
        w, h = img.width // cols, img.height // rows
        pos = k % per
        if pos // cols >= rows or (pos // cols + 1) * h > img.height:
            return None
        tile = img.crop((pos % cols * w, pos // cols * h, pos % cols * w + w, pos // cols * h + h))
        return tile.resize((round(tile.width * THUMB_H / tile.height), THUMB_H))


def clip_seconds(seg: dict) -> list[float]:
    if "orig" in seg:
        return [float(seg["orig"][0])]
    return [float(c[0] if isinstance(c, list) else c) for c in seg.get("clips", [])][:3]


def main() -> None:
    job = Path(sys.argv[1])
    script = json.loads((job / (sys.argv[2] if len(sys.argv) > 2 else "script.json")).read_text(encoding="utf-8"))
    sheets = job / "sheets"
    has_frames = any(sheets.glob("*_sheet_*.jpg")) if sheets.exists() else False  # storyboard pages do not count
    if not has_frames and job.name.endswith("-promo"):  # a promo reuses its full video's source and sheets
        sheets = job.with_name(job.name[: -len("-promo")]) / "sheets"
    frames = Frames(sheets)
    font = ImageFont.truetype(FONT, 20) if FONT else ImageFont.load_default()
    segs = script["segments"]
    for page in range(0, len(segs), PER_PAGE):
        rows = segs[page:page + PER_PAGE]
        board = Image.new("RGB", (TEXT_W + 3 * 260, len(rows) * (THUMB_H + 10)), "black")
        d = ImageDraw.Draw(board)
        for r, seg in enumerate(rows):
            y = r * (THUMB_H + 10)
            x = 0
            for sec in clip_seconds(seg):
                t = frames.tile(sec)
                if t:
                    board.paste(t, (x, y))
                    d.text((x + 4, y + THUMB_H - 26), f"{sec:.0f}s", font=font, fill="yellow")
                    x += t.width + 4
            words = seg.get("say") or f"[ORIGINAL] {seg.get('text', '')}"
            label = f"#{page + r + 1}  " + words.replace("*", "")
            d.multiline_text((3 * 260 + 10, y + 6), "\n".join(textwrap.wrap(label, 52)[:5]), font=font, fill="white")
        out = job / "sheets"; out.mkdir(exist_ok=True)
        board.save(out / f"storyboard_{page // PER_PAGE + 1:02d}.jpg", quality=85)
    print(f"{(len(segs) + PER_PAGE - 1) // PER_PAGE} storyboard pages in {job / 'sheets'}")


if __name__ == "__main__":
    main()
