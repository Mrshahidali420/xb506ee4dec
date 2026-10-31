"""Anime Rewind Recap brand images, in the Spoiler Room style: round badge with ring text, glowing centre mark,
and a cover with a tilted film strip of tinted anime frames behind a big 3D title.

python brand/make_anime_brand.py  -> brand/anime-rewind/{profile,cover,logo}.png
Cover frames: brand/anime-rewind/frames/f*.jpg (stills pulled from an anime source with ffmpeg).
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

OUT = Path(__file__).resolve().parent / "anime-rewind"
BLACK = "C:/Windows/Fonts/ariblk.ttf"
IMPACT = "C:/Windows/Fonts/impact.ttf"
BG, CREAM = (20, 14, 30), (255, 248, 236)
PINK, VIOLET, CYAN = (255, 62, 150), (120, 60, 230), (70, 220, 255)


def glow(layer: Image.Image, radius: int) -> Image.Image:
    return Image.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)), layer)


def ring_text(img: Image.Image, text: str, center: tuple, radius: float, size: int) -> None:
    """Write text around a circle, clockwise, starting at the top-left like the Spoiler Room badge."""
    font = ImageFont.truetype(BLACK, size)
    widths = [font.getlength(c) for c in text]
    angle = -90 - math.degrees(sum(widths) / radius) / 2
    for ch, w in zip(text, widths):
        mid = angle + math.degrees(w / 2 / radius)
        tile = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((size, size), ch, font=font, fill=CREAM, anchor="mm")
        tile = tile.rotate(-(mid + 90), resample=Image.BICUBIC)
        x = center[0] + radius * math.cos(math.radians(mid)) - size
        y = center[1] + radius * math.sin(math.radians(mid)) - size
        img.alpha_composite(tile, (int(x), int(y)))
        angle += math.degrees(w / radius)


def anime_eye(size: int) -> Image.Image:
    """A big shiny anime eye whose pupil is a rewind sign: the page mark."""
    s = size
    cx, cy = s / 2, s / 2
    a, b = s * 0.47, s * 0.26          # half width and half height of the almond
    big = (a * a + b * b) / (2 * b)    # radius of the two lid circles
    top_c, bot_c = cy + big - b, cy - big + b
    upper = Image.new("L", (s, s), 0)
    ImageDraw.Draw(upper).ellipse((cx - big, top_c - big, cx + big, top_c + big), fill=255)
    lower = Image.new("L", (s, s), 0)
    ImageDraw.Draw(lower).ellipse((cx - big, bot_c - big, cx + big, bot_c + big), fill=255)
    almond = ImageChops.multiply(upper, lower)
    eye = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    eye.paste(Image.new("RGBA", (s, s), CREAM + (255,)), (0, 0), almond)
    # iris: violet-to-pink gradient disc, cut by the lids
    r = b * 0.98
    grad = Image.linear_gradient("L").resize((s, s))
    colour = Image.composite(Image.new("RGB", (s, s), PINK), Image.new("RGB", (s, s), VIOLET), grad)
    disc = Image.new("L", (s, s), 0)
    ImageDraw.Draw(disc).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    eye.paste(colour.convert("RGBA"), (0, 0), ImageChops.multiply(disc, almond))
    d = ImageDraw.Draw(eye)
    # rewind pupil
    h = r * 0.95
    for x0 in (cx + r * 0.08, cx + r * 0.62):
        d.polygon([(x0, cy - h / 2), (x0, cy + h / 2), (x0 - r * 0.58, cy)], fill=(18, 8, 28))
    # anime shine
    d.ellipse((cx - r * 0.8, cy - r * 0.78, cx - r * 0.3, cy - r * 0.28), fill=(255, 255, 255, 240))
    d.ellipse((cx + r * 0.38, cy + r * 0.38, cx + r * 0.62, cy + r * 0.62), fill=(255, 255, 255, 210))
    # dark lid lines: thick upper, thin lower
    lw = max(5, s // 40)
    phi = math.degrees(math.asin(a / big))
    d.arc((cx - big, top_c - big, cx + big, top_c + big), -90 - phi, -90 + phi, fill=(12, 6, 20), width=lw * 2)
    d.arc((cx - big, bot_c - big, cx + big, bot_c + big), 90 - phi, 90 + phi, fill=(12, 6, 20), width=lw)
    # lashes, pointing out from the upper lid
    for frac in (-0.95, -0.7, 0.7, 0.95):
        t = math.radians(-90 + frac * phi)
        bx, by = cx + big * math.cos(t), top_c + big * math.sin(t)
        tip = (bx + math.cos(t) * s * 0.09 + (s * 0.03 if frac > 0 else -s * 0.03), by + math.sin(t) * s * 0.09)
        d.line((bx, by, *tip), fill=(12, 6, 20), width=lw)
    return eye


def profile_image() -> Image.Image:
    size = 1080
    img = Image.new("RGBA", (size, size), BG + (255,))
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((260, 260, 820, 820), fill=(110, 30, 120, 160))
    img = Image.alpha_composite(img, halo.filter(ImageFilter.GaussianBlur(120)))
    d = ImageDraw.Draw(img)
    d.ellipse((60, 60, 1020, 1020), outline=CREAM, width=14)
    d.ellipse((165, 165, 915, 915), outline=CREAM, width=6)
    ring_text(img, "ANIME REWIND  •  FULL ANIME RECAPS  •", (540, 540), 425, 66)
    eye = glow(anime_eye(560), 22)
    img.alpha_composite(eye, (260, 200))
    f = ImageFont.truetype(IMPACT, 92)
    d = ImageDraw.Draw(img)
    d.text((546, 746), "REWIND", font=f, fill=PINK, anchor="mm")
    d.text((540, 740), "REWIND", font=f, fill=CREAM, anchor="mm")
    return img


def film_strip(frames: list, width: int, height: int) -> Image.Image:
    """Frames side by side between sprocket rows, tinted in the page colours."""
    fh = height - 80
    strip = Image.new("RGBA", (width, height), (22, 16, 32, 255))
    x = 0
    for p in frames:
        im = ImageOps.fit(Image.open(p).convert("RGB"), (int(fh * 1.45), fh))
        tint = ImageOps.colorize(ImageOps.grayscale(im), (25, 5, 45), (255, 90, 175))
        strip.paste(tint, (x, 40))
        x += im.width + 14
        if x > width:
            break
    d = ImageDraw.Draw(strip)
    for sx in range(10, width, 44):
        d.rounded_rectangle((sx, 12, sx + 20, 30), 3, fill=(70, 60, 90))
        d.rounded_rectangle((sx, height - 30, sx + 20, height - 12), 3, fill=(70, 60, 90))
    return strip


def title_3d(img: Image.Image, text: str, center: tuple, size: int) -> None:
    f = ImageFont.truetype(IMPACT, size)
    d = ImageDraw.Draw(img)
    for k in range(14, 0, -1):
        d.text((center[0] + k, center[1] + k), text, font=f, fill=(150, 20, 90) if k > 2 else PINK, anchor="mm")
    d.text(center, text, font=f, fill=CREAM, anchor="mm")


def cover_image() -> Image.Image:
    w, h = 1640, 624
    img = Image.new("RGBA", (w, h), BG + (255,))
    frames = sorted((OUT / "frames").glob("f*.jpg"), key=lambda p: int(p.stem[1:]))
    for row, (dy, picks) in enumerate(((-150, frames[:6]), (150, frames[6:]))):
        strip = film_strip(picks, 2400, 330).rotate(-7, expand=True, resample=Image.BICUBIC)
        img.alpha_composite(strip, ((w - strip.width) // 2 - 120 + row * 160, (h - strip.height) // 2 + dy))
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(shade).ellipse((180, 110, 1460, 520), fill=(10, 5, 18, 190))
    img = Image.alpha_composite(img, shade.filter(ImageFilter.GaussianBlur(70)))
    title_3d(img, "ANIME REWIND", (w // 2, 285), 210)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(BLACK, 40)
    sub = "FULL ANIME STORIES IN MINUTES"
    tw = f.getlength(sub)
    d.rectangle((w / 2 - tw / 2 - 26, 420, w / 2 + tw / 2 + 26, 476), fill=PINK)
    d.text((w / 2, 448), sub, font=f, fill=CREAM, anchor="mm")
    mark = anime_eye(130)
    img.alpha_composite(glow(mark, 8), (w // 2 - 65, 485))
    return img


def main() -> None:
    profile = profile_image()
    profile.convert("RGB").save(OUT / "profile.png")
    cover_image().convert("RGB").save(OUT / "cover.png")
    logo = profile.resize((512, 512), Image.LANCZOS)
    mask = Image.new("L", logo.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 511, 511), fill=255)
    round_logo = Image.new("RGBA", logo.size, (0, 0, 0, 0))
    round_logo.paste(logo, (0, 0), mask)
    round_logo.save(OUT / "logo.png")


if __name__ == "__main__":
    main()
