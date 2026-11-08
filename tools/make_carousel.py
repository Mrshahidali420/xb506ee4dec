"""Make a swipe-through picture post (carousel) from a built manhwa part, by code.

Usage: python make_carousel.py <job>
Needs <job>/panels/*.png (the build made them) and <job>/script.json.
Writes <job>/posts/card_1.jpg ... card_N.jpg (4:5) and <job>/posts/post.json {caption, comment}.

Cards: the hook panel, 4 panels spread over the story (each with its script line), and an end card.
A clip number is a second of the source video; one panel is PANEL_SECONDS long (as in storyboard.py).
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1080, 1350
PANEL_SECONDS = 3
STORY_CARDS = 4
FONTS = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"]


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def first_clip(seg: dict) -> float:
    clip = seg["clips"][0]
    return float(clip[0] if isinstance(clip, list) else clip)


def card(panel: Path, line: str) -> Image.Image:
    """Blurred copy of the panel as the backdrop, the sharp panel on top, the script line on a dark band."""
    art = Image.open(panel).convert("RGB")
    back = art.resize((W, H)).filter(ImageFilter.GaussianBlur(28))
    sharp = art.copy()
    sharp.thumbnail((W, H - 260))
    page = back.point(lambda v: int(v * 0.45))
    page.paste(sharp, ((W - sharp.width) // 2, 30))
    band = Image.new("RGBA", (W, 250), (0, 0, 0, 175))
    page.paste(band, (0, H - 250), band)
    draw = ImageDraw.Draw(page)
    text = textwrap.fill(line, 34)
    draw.multiline_text((W // 2, H - 125), text, font=font(44), fill="white", anchor="mm", align="center", spacing=10)
    return page


def end_card(title: str) -> Image.Image:
    page = Image.new("RGB", (W, H), (14, 14, 20))
    draw = ImageDraw.Draw(page)
    draw.multiline_text((W // 2, H // 2 - 60), textwrap.fill(title, 22), font=font(70), fill="white", anchor="mm",
                        align="center", spacing=16)
    draw.text((W // 2, H // 2 + 200), "Full recap on our page - follow for the next part", font=font(40),
              fill=(255, 196, 70), anchor="mm")
    return page


def main() -> None:
    job = ROOT / sys.argv[1]
    script = json.loads((job / "script.json").read_text(encoding="utf-8"))
    panels = sorted((job / "panels").glob("p_*.png"))
    if not panels:
        sys.exit("no panels in this job: carousel skipped")
    segs = script["segments"]
    body = segs[1:-1] or segs
    picks = [segs[0]] + [body[round(i * (len(body) - 1) / (STORY_CARDS - 1))] for i in range(STORY_CARDS)]
    out = job / "posts"
    out.mkdir(exist_ok=True)
    used = set()
    n = 0
    for seg in picks:
        idx = min(int(first_clip(seg) // PANEL_SECONDS), len(panels) - 1)
        if idx in used:
            continue
        used.add(idx)
        n += 1
        card(panels[idx], seg["say"].replace("*", "")).save(out / f"card_{n}.jpg", quality=88)
    end_card(script["title"].split(" . . .")[0]).save(out / f"card_{n + 1}.jpg", quality=88)
    post = {"caption": script.get("caption", script["title"]), "comment": script.get("comment", ""), "cards": n + 1}
    (out / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{job.name}: {n + 1} cards")


if __name__ == "__main__":
    main()
