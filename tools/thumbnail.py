"""Recap thumbnail in the reference style: two faces, big yellow labels with arrows, a bold title.

python tools/thumbnail.py <spec.json> <out.png>
spec: {"size": [1280, 720], "left": {"img": ..., "crop": [x0, y0, x1, y1], "label": "..."},
       "right": {...}, "center": {"img": ..., "crop": [...]} (optional), "title": "..."}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

IMPACT = "C:/Windows/Fonts/impact.ttf" if Path("C:/Windows/Fonts/impact.ttf").exists() else \
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
YELLOW, BLACK = (255, 214, 0), (0, 0, 0)


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def side(part: dict, w: int, h: int) -> Image.Image:
    img = Image.open(part["img"]).convert("RGB").crop(tuple(part["crop"]))
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.35)).enhance(1.15)
    return cover(img, w, h)


def outlined(d: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, fill, anchor="mm") -> None:
    font = ImageFont.truetype(IMPACT, size)
    d.text(xy, text, font=font, fill=fill, anchor=anchor, stroke_width=max(4, size // 12), stroke_fill=BLACK)


def label(canvas: Image.Image, text: str, box_xy: tuple[int, int], tip: tuple[int, int]) -> None:
    d = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(IMPACT, 64)
    l, t, r, b = d.textbbox(box_xy, text, font=font, anchor="mm")
    d.line([box_xy, tip], fill=BLACK, width=22)
    d.line([box_xy, tip], fill=YELLOW, width=12)
    vx, vy = tip[0] - box_xy[0], tip[1] - box_xy[1]
    n = (vx * vx + vy * vy) ** 0.5 or 1
    ux, uy = vx / n, vy / n
    head = [tip, (tip[0] - 46 * ux + 26 * uy, tip[1] - 46 * uy - 26 * ux), (tip[0] - 46 * ux - 26 * uy, tip[1] - 46 * uy + 26 * ux)]
    d.polygon(head, fill=YELLOW, outline=BLACK, width=5)
    d.rounded_rectangle((l - 22, t - 14, r + 22, b + 14), 10, fill=YELLOW, outline=BLACK, width=6)
    d.text(box_xy, text, font=font, fill=BLACK, anchor="mm")


def main() -> None:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    W, H = spec.get("size", [1280, 720])
    canvas = Image.new("RGB", (W, H))
    half = W // 2
    canvas.paste(side(spec["left"], half, H), (0, 0))
    canvas.paste(side(spec["right"], W - half, H), (half, 0))
    d = ImageDraw.Draw(canvas)
    d.rectangle((half - 6, 0, half + 6, H), fill=BLACK)  # hard split, like the references
    if spec.get("center"):
        c = side(spec["center"], 300, 300)
        mask = Image.new("L", c.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 299, 299), fill=255)
        glow = Image.new("RGBA", (380, 380), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((20, 20, 360, 360), fill=(120, 255, 60, 200))
        canvas.paste(glow.filter(ImageFilter.GaussianBlur(22)), (half - 190, H // 2 - 170), glow.filter(ImageFilter.GaussianBlur(22)))
        canvas.paste(c, (half - 150, H // 2 - 130), mask)
    label(canvas, spec["left"]["label"], (240, 120), (300, 300))
    label(canvas, spec["right"]["label"], (W - 250, 120), (W - 330, 300))
    outlined(ImageDraw.Draw(canvas), (W // 2, H - 80), spec["title"], 104, (255, 255, 255))
    canvas.save(sys.argv[2])


if __name__ == "__main__":
    main()
