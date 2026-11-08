"""AI thumbnail for a manhwa long video: Gemini paints the scene (the official cover is the character reference),
then the code writes the words on the empty left third.

Usage: python thumb_ai.py <meta dir>
Reads <meta dir>/thumb.json:
  {"series": "name for AniList", "scene": "who and what is in the picture, this part's big moment",
   "labels": ["2-3 big lines, 1-3 words each"], "badge": "CH 26-30"}
A ready <meta dir>/art.png (painted by hand in an app) is used instead of Gemini.
Writes <meta dir>/art.png (the painting) and <meta dir>/thumbnail.jpg. Needs GEMINI_API_KEY (env or ~/.config/watch/.env).
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thumbnail import BLACK, IMPACT, YELLOW, cover  # noqa: E402
from thumb_part import anilist_cover  # noqa: E402

W, H = 1280, 720
MODELS = ["gemini-3.1-flash-image", "gemini-3-pro-image", "gemini-2.5-flash-image"]
STYLE = ("Premium Korean manhwa illustration style, 16:9 widescreen composition. {scene} "
         "Draw the main character exactly like the character on the reference cover (same face, hair, outfit). "
         "Extreme emotional contrast, cinematic low-angle shot, dramatic rim lighting, vibrant glow against a deep moody "
         "shadow backdrop, hyper-detailed, high CTR YouTube thumbnail style. Keep the main subject in the right two thirds. "
         "Leave clean, dark, empty negative space on the left third of the frame for text. "
         "No text, no letters, no numbers, no watermarks, no logos.")


def key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = Path.home() / ".config/watch/.env"
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    sys.exit("no GEMINI_API_KEY")


def paint(scene: str, ref: Image.Image) -> Image.Image:
    buf = io.BytesIO()
    ref.save(buf, "JPEG", quality=90)
    body = {"contents": [{"parts": [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
                                    {"text": STYLE.format(scene=scene)}]}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}}}
    errors = []
    for model in MODELS:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": key()}, json=body, timeout=180)
        data = r.json()
        for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
            if "inlineData" in part:
                print("painted with", model)
                return Image.open(io.BytesIO(base64.b64decode(part["inlineData"]["data"]))).convert("RGB")
        errors.append(f"{model}: {data.get('error', {}).get('message', 'no image')[:120]}")
    sys.exit("no picture: " + " | ".join(errors))


def words(img: Image.Image, labels: list[str], badge: str) -> Image.Image:
    d = ImageDraw.Draw(img)
    fills = [YELLOW, (255, 255, 255), YELLOW]
    sizes = [120 if len(t) <= 10 else 96 if len(t) <= 14 else 80 for t in labels]
    y = (H - sum(s + 18 for s in sizes)) // 2
    for text, size, fill in zip(labels, sizes, fills):
        font = ImageFont.truetype(IMPACT, size)
        d.text((44, y), text, font=font, fill=fill, stroke_width=max(5, size // 11), stroke_fill=BLACK)
        y += size + 18
    font = ImageFont.truetype(IMPACT, 38)  # a small badge: the picture is the star
    l, t, r, b = d.textbbox((0, 0), badge, font=font)
    x0, y0 = W - (r - l) - 52, H - (b - t) - 42
    d.rounded_rectangle((x0, y0, x0 + (r - l) + 24, y0 + (b - t) + 18), 8, fill=YELLOW, outline=BLACK, width=4)
    d.text((x0 + 12, y0 + 9 - t), badge, font=font, fill=BLACK)
    return img


def main() -> None:
    meta = Path(sys.argv[1])
    spec = json.loads((meta / "thumb.json").read_text(encoding="utf-8"))
    if (meta / "art.png").exists():  # a painting made by hand (Gemini or ChatGPT app) is used as it is
        art = Image.open(meta / "art.png").convert("RGB")
    else:
        art = paint(spec["scene"], anilist_cover(spec["series"]))
        art.save(meta / "art.png")
    words(cover(art, W, H), spec["labels"], spec["badge"]).save(meta / "thumbnail.jpg", quality=92)
    print("thumbnail:", meta / "thumbnail.jpg")


if __name__ == "__main__":
    main()
