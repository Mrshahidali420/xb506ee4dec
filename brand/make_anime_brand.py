"""Anime Rewind Recap brand images, in the Manhwa X style: dark navy stripes, glowing frame, rewind mark.

python brand/make_anime_brand.py  -> brand/anime-rewind/{profile,cover,logo}.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent / "anime-rewind"
FONT = "C:/Windows/Fonts/arialbd.ttf"
NAVY, LINE = (16, 22, 48), (34, 48, 98)
PINK, CYAN, PALE = (255, 70, 150), (60, 215, 255), (200, 240, 255)


def stripes(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), NAVY)
    d = ImageDraw.Draw(img)
    for x in range(-h, w, 26):
        d.line((x, h, x + h, 0), fill=LINE, width=3)
    return img


def rewind(size: int) -> Image.Image:
    """Two overlapping triangles pointing left (rewind), pink over cyan, with a soft glow."""
    mark = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(mark)
    h = size * 0.62
    top, bot = (size - h) / 2, (size + h) / 2
    d.polygon([(size * 0.52, top), (size * 0.52, bot), (size * 0.08, size / 2)], fill=CYAN)
    d.polygon([(size * 0.94, top), (size * 0.94, bot), (size * 0.50, size / 2)], fill=PINK)
    glow = mark.filter(ImageFilter.GaussianBlur(size // 18))
    return Image.alpha_composite(glow, mark)


def framed(img: Image.Image, box: tuple, top_word: str, bottom_word: str, mark_size: int) -> Image.Image:
    x0, y0, x1, y1 = box
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rectangle(box, outline=CYAN + (255,), width=10)
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(14)))
    d = ImageDraw.Draw(img)
    d.rectangle(box, fill=(20, 26, 56), outline=CYAN, width=6)
    band = int((y1 - y0) * 0.13)
    d.rectangle((x0 + 40, y0 + 30, x1 - 40, y0 + 30 + band), fill=(32, 70, 130))
    f1 = ImageFont.truetype(FONT, int(band * 0.72))
    d.text(((x0 + x1) / 2, y0 + 30 + band / 2), top_word, font=f1, fill="white", anchor="mm")
    m = rewind(mark_size)
    img.alpha_composite(m, (int((x0 + x1 - mark_size) / 2), int((y0 + y1 - mark_size) / 2 + band * 0.1)))
    f2 = ImageFont.truetype(FONT, int(band * 0.95))
    ImageDraw.Draw(img).text(((x0 + x1) / 2, y1 - band * 0.95), bottom_word, font=f2, fill=PALE, anchor="mm")
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    profile = framed(stripes(1080, 1080), (175, 220, 905, 840), "ANIME REWIND", "RECAP", 340)
    profile.convert("RGB").save(OUT / "profile.png")
    cover = stripes(1702, 630).convert("RGBA")
    cover = framed(cover, (520, 70, 1182, 560), "ANIME REWIND", "RECAP", 230)
    d = ImageDraw.Draw(cover)
    small = ImageFont.truetype(FONT, 44)
    d.text((260, 315), "Full anime stories", font=small, fill=PALE, anchor="mm")
    d.text((1442, 315), "Explained fast", font=small, fill=PALE, anchor="mm")
    cover.convert("RGB").save(OUT / "cover.png")
    logo = profile.resize((512, 512), Image.LANCZOS)
    mask = Image.new("L", logo.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 511, 511), fill=255)
    round_logo = Image.new("RGBA", logo.size, (0, 0, 0, 0))
    round_logo.paste(logo, (0, 0), mask)
    round_logo.save(OUT / "logo.png")


if __name__ == "__main__":
    main()
