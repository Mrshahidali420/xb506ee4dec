"""Page header for the empty band above a vertical video: round logo + page name + @handle.

python tools/brand_header.py <logo.png> "<NAME>" "<@handle>" "<tagline>" "<r,g,b accent>" <out.png>
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path("C:/Windows/Fonts") if Path("C:/Windows/Fonts").exists() else Path("/usr/share/fonts/truetype/dejavu")
IMPACT = str(FONT_DIR / "impact.ttf") if (FONT_DIR / "impact.ttf").exists() else str(FONT_DIR / "DejaVuSans-Bold.ttf")
BOLD = str(FONT_DIR / "arialbd.ttf") if (FONT_DIR / "arialbd.ttf").exists() else str(FONT_DIR / "DejaVuSans-Bold.ttf")
W, H, LOGO = 980, 250, 210


def main() -> None:
    logo_path, name, handle, tagline, accent, out = sys.argv[1:7]
    acc = tuple(int(v) for v in accent.split(","))
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    # dark glass card with an accent glow behind the logo
    d.rounded_rectangle((60, 34, W - 6, H - 34), 40, fill=(8, 8, 10, 190), outline=acc + (230,), width=4)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((0, 0, LOGO + 40, H), fill=acc + (170,))
    card = Image.alpha_composite(card, glow.filter(ImageFilter.GaussianBlur(22)))
    logo = Image.open(logo_path).convert("RGBA").resize((LOGO, LOGO), Image.LANCZOS)
    card.alpha_composite(logo, (20, (H - LOGO) // 2))
    d = ImageDraw.Draw(card)
    x = LOGO + 50
    d.text((x, 44), name, font=ImageFont.truetype(IMPACT, 86), fill="white", stroke_width=3, stroke_fill="black")
    d.text((x + 2, 138), handle, font=ImageFont.truetype(BOLD, 34), fill=acc)
    d.text((x + 2, 180), tagline, font=ImageFont.truetype(BOLD, 24), fill=(200, 200, 200))
    card.save(out)


if __name__ == "__main__":
    main()
