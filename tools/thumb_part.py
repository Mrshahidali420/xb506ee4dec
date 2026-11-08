"""YouTube thumbnail for a manhwa part: official cover, 3 arrow labels, a chapter badge. No sentence.

Usage: python thumb_part.py <meta dir> "<series name for AniList>"
Reads <meta dir>/thumb.json {"labels": ["3 SHORT LABELS", ...], "badge": "CH 26-30"}; writes <meta dir>/thumbnail.jpg.
Same look as jobs/manhwa-swordmaster-son-full/thumbnail.jpg.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thumbnail import BLACK, IMPACT, YELLOW  # noqa: E402

W, H = 1280, 720
GREEN = (60, 220, 70)
FILLS = [YELLOW, GREEN, YELLOW]


def anilist_cover(name: str) -> Image.Image:
    q = "query($s:String){Media(search:$s,type:MANGA){coverImage{extraLarge}}}"
    r = requests.post("https://graphql.anilist.co", json={"query": q, "variables": {"s": name}}, timeout=30).json()
    return Image.open(io.BytesIO(requests.get(r["data"]["Media"]["coverImage"]["extraLarge"], timeout=30).content)).convert("RGB")


def box(d: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], fill, size: int = 64) -> tuple[int, int, int, int]:
    font = ImageFont.truetype(IMPACT, size)
    l, t, r, b = d.textbbox(xy, text, font=font, anchor="lm")
    d.rounded_rectangle((l - 18, t - 12, r + 18, b + 12), 10, fill=fill, outline=BLACK, width=6)
    d.text(xy, text, font=font, fill=BLACK, anchor="lm")
    return l - 18, t - 12, r + 18, b + 12


def arrow(d: ImageDraw.ImageDraw, start: tuple[int, int], tip: tuple[int, int], fill) -> None:
    d.line([start, tip], fill=BLACK, width=20)
    d.line([start, tip], fill=fill, width=10)
    vx, vy = tip[0] - start[0], tip[1] - start[1]
    n = (vx * vx + vy * vy) ** 0.5 or 1
    ux, uy = vx / n, vy / n
    d.polygon([tip, (tip[0] - 44 * ux + 24 * uy, tip[1] - 44 * uy - 24 * ux), (tip[0] - 44 * ux - 24 * uy, tip[1] - 44 * uy + 24 * ux)],
              fill=fill, outline=BLACK, width=5)


def main() -> None:
    meta = Path(sys.argv[1])
    spec = json.loads((meta / "thumb.json").read_text(encoding="utf-8"))
    art = anilist_cover(sys.argv[2])
    if spec.get("crop"):  # [x0, y0, x1, y1] as fractions: a wrap-around cover is cut down to its art
        x0, y0, x1, y1 = spec["crop"]
        art = art.crop((round(art.width * x0), round(art.height * y0), round(art.width * x1), round(art.height * y1)))
    back = art.resize((W, H)).filter(ImageFilter.GaussianBlur(24)).point(lambda v: int(v * 0.55))
    s = H / art.height
    sharp = art.resize((round(art.width * s), H), Image.LANCZOS)
    if sharp.width < 560:  # a narrow cover: zoom in on its top so the art is not a thin strip
        z = 600 / art.width
        sharp = art.resize((600, round(art.height * z)), Image.LANCZOS).crop((0, 0, 600, H))
    left = W - sharp.width - 40
    back.paste(sharp, (left, 0))
    d = ImageDraw.Draw(back)
    for i, (text, y) in enumerate(zip(spec["labels"][:3], (105, 340, 575))):
        r = box(d, text, (40, y), FILLS[i])
        arrow(d, (r[2] + 4, y), (left + 130 + (i % 2) * 60, y + (45 if i == 0 else 0) - (45 if i == 2 else 0)), FILLS[i])
    bw = d.textbbox((0, 0), spec["badge"], font=ImageFont.truetype(IMPACT, 60))
    box(d, spec["badge"], (W - 40 - (bw[2] - bw[0]) - 18, H - 56), YELLOW, 60)
    back.save(meta / "thumbnail.jpg", quality=92)
    print("thumbnail:", meta / "thumbnail.jpg")


if __name__ == "__main__":
    main()
