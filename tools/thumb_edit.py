"""Thumbnails for a manhwa long video, made by photo editing (no AI painting). Runs on GitHub (thumb.yml).

Three different styles from the same part, so Shahid can pick (or A/B test in YouTube Studio):
  shock   the part's most shocking panel blown up, red danger tone, one huge word, a red circle on the key spot
  versus  the hero (cut out of the official cover, cyan) against the panel's threat (red), torn diagonal split
  power   the hero with a huge glowing spectral copy behind him, RPG status window, level-up arrows,
          enemies in the dark, the panel as the backdrop (Shahid's prompt, 8 Nov 2026)
All have minimum text (1-3 words) and a small chapter badge.

How: real panels come from the part's source.mp4 (one panel per 3 s; the build cache keeps it),
rembg cuts characters out of the AniList art (BiRefNet, then the anime model), the layers are put together
in HTML/CSS and Chromium (Playwright) takes the 1280x720 screenshot.

Usage: python thumb_edit.py <meta dir>
<meta dir>/thumb.json:
  {"series": "name for AniList", "word": "KILL ME", "badge": "CH 26-30",
   "panels": [1380, 1188]   seconds in source.mp4: the shocking moment first, a second strong one next,
   "spot": [0.5, 0.4]       where the red circle goes on the first panel, as fractions (optional),
   "crop": [x0,y0,x1,y1]    fractions of the cover, to cut its printed title off (optional)}
The job folder is the parent of <meta dir>. Writes thumb-shock.jpg, thumb-versus.jpg, thumb-power.jpg there.
"""
from __future__ import annotations

import html
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image
from playwright.sync_api import sync_playwright
from rembg import new_session, remove

W, H = 1280, 720
PANEL_SECONDS = 3
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
MODELS = ("birefnet-general", "isnet-anime", "isnet-general-use")
QUERY = ("query($s:String){Media(search:$s,type:MANGA){coverImage{extraLarge} "
         "characters(sort:ROLE,perPage:4){nodes{image{large}}}}}")


def fetch(url: str) -> Image.Image:
    return Image.open(io.BytesIO(requests.get(url, timeout=30).content)).convert("RGB")


def cutout(img: Image.Image, sessions: dict) -> Image.Image:
    """The figure without its background. A model that keeps almost all or almost none of it failed: try the next."""
    best = None
    for name in MODELS:
        sessions.setdefault(name, new_session(name))
        cut = remove(img, session=sessions[name], post_process_mask=True).convert("RGBA")
        kept = sum(cut.getchannel("A").point(lambda v: 1 if v > 128 else 0).getdata()) / (img.width * img.height)
        print(f"  {name}: keeps {kept:.0%}")
        best = best or cut
        if 0.08 < kept < 0.75:
            best = cut
            break
    return best.crop(best.getbbox() or (0, 0, best.width, best.height))


def panel(video: Path, second: float, to: Path) -> Path:
    """The middle frame of the panel shown at `second`, with the video's black letterbox cut off."""
    mid = int(second // PANEL_SECONDS) * PANEL_SECONDS + PANEL_SECONDS / 2
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{mid:.2f}", "-i", str(video), "-frames:v", "1", str(to)], check=True)
    img = Image.open(to).convert("RGB")
    box = img.convert("L").point(lambda v: 255 if v > 22 else 0).getbbox()
    if box and box[2] - box[0] > img.width // 3:
        img = img.crop(box)
    img.save(to)
    return to


BASE = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&display=block" rel="stylesheet">
<style>
 * {{ margin: 0; box-sizing: border-box; }}
 body {{ width: 1280px; height: 720px; overflow: hidden; background: #000; position: relative; font-family: Anton, Impact, sans-serif; }}
 .a {{ position: absolute; }}
 .word {{ white-space: nowrap; line-height: .95; -webkit-text-stroke: 10px #000; paint-order: stroke fill; letter-spacing: 1px; }}
 .badge {{ right: 30px; bottom: 26px; background: #ffd600; color: #000; font-size: 30px; padding: 3px 11px 1px;
          border: 4px solid #000; border-radius: 8px; }}
 {css}
</style></head><body>{body}<div class="a badge">{badge}</div></body></html>"""

SHOCK_CSS = """
 .bg { inset: -40px; background: url(p0.png) center/cover; filter: blur(14px) brightness(.35) saturate(1.4); }
 .pic { right: 0; top: 0; height: 720px; width: 900px; object-fit: cover; object-position: center 35%;
        filter: contrast(1.25) saturate(1.35) brightness(1.05); -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 22%); }
 .red { inset: 0; background: radial-gradient(ellipse 60% 80% at 70% 50%, transparent 40%, rgba(120,0,0,.55) 100%),
        linear-gradient(90deg, rgba(0,0,0,.95) 0%, rgba(0,0,0,.6) 30%, transparent 45%); }
 .ring { width: 230px; height: 230px; border: 12px solid #ff1e1e; border-radius: 50%;
         box-shadow: 0 0 0 5px #000, 0 0 30px #ff1e1e, inset 0 0 0 5px #000; }
 .hero { left: 10px; bottom: -10px; height: 470px; filter: drop-shadow(0 0 3px #fff) drop-shadow(0 0 22px rgba(255,40,40,.8)); }
 .word { left: 36px; top: 46px; color: #fff; font-size: 150px; text-shadow: 0 0 30px rgba(255,0,0,.7); }
"""

VERSUS_CSS = """
 .left { left: 0; top: 0; width: 760px; height: 720px; background: radial-gradient(circle at 40% 50%, #0b4f86, #020a18 70%);
         clip-path: polygon(0 0, 100% 0, 72% 100%, 0 100%); }
 .right { right: 0; top: 0; width: 760px; height: 720px; clip-path: polygon(28% 0, 100% 0, 100% 100%, 0 100%); overflow: hidden; }
 .right img { width: 100%; height: 100%; object-fit: cover; object-position: center 35%; filter: contrast(1.3) saturate(1.2) sepia(.35) hue-rotate(-25deg) brightness(.95); }
 .right:after { content: ""; position: absolute; inset: 0; background: linear-gradient(90deg, rgba(160,0,0,.55), rgba(60,0,0,.15)); mix-blend-mode: multiply; }
 .tear { left: 510px; top: -20px; width: 26px; height: 780px; background: #fff; transform: skewX(-19.5deg);
         box-shadow: 0 0 25px #fff, 0 0 60px #28d2ff, 0 0 60px #ff2a2a; }
 .hero { left: 40px; bottom: -8px; height: 700px; filter: contrast(1.1) saturate(1.3) drop-shadow(0 0 3px #bff) drop-shadow(0 0 26px #28d2ff); }
 .word { left: 50%; bottom: 30px; transform: translateX(-50%); color: #ffd600; font-size: 120px; }
"""

POWER_CSS = """
 .bg { inset: -40px; background: url(p1.png) center/cover; filter: blur(8px) brightness(.3) saturate(1.3); }
 .tone { inset: 0; background: radial-gradient(ellipse 45% 70% at 62% 55%, rgba(40,210,255,.35), transparent 70%),
        linear-gradient(90deg, rgba(0,0,0,.9) 0%, rgba(0,0,0,.55) 30%, transparent 50%),
        radial-gradient(ellipse at 50% 50%, transparent 55%, rgba(0,0,0,.85) 100%); }
 .foe { top: 0; height: 290px; filter: brightness(.35) sepia(1) hue-rotate(-35deg) saturate(4) drop-shadow(0 0 10px rgba(255,40,60,.7)); }
 .ghost { height: 1080px; top: -230px; left: 790px; transform: translateX(-50%); opacity: .55; mix-blend-mode: screen;
          filter: grayscale(1) brightness(1.6) sepia(1) hue-rotate(150deg) saturate(6) blur(1px) drop-shadow(0 0 40px #28d2ff); }
 .hero { height: 650px; bottom: -6px; left: 800px; transform: translateX(-50%);
         filter: contrast(1.12) saturate(1.25) drop-shadow(0 0 2px #bff) drop-shadow(0 0 16px #28d2ff) drop-shadow(0 0 40px rgba(40,210,255,.6)); }
 .status { left: 1030px; top: 90px; width: 210px; height: 136px; border: 3px solid #28d2ff; border-radius: 12px;
           background: rgba(8,50,110,.55); box-shadow: 0 0 22px #28d2ff, inset 0 0 18px rgba(40,210,255,.4); padding: 38px 15px 0; }
 .status:before { content: ""; position: absolute; left: 12px; right: 12px; top: 24px; height: 2px; background: #28d2ff; }
 .bar { height: 12px; border-radius: 6px; background: linear-gradient(90deg, #28d2ff, #9ff); margin: 0 0 16px; box-shadow: 0 0 8px #28d2ff; }
 .up { color: #7ef; font-size: 64px; font-family: sans-serif; text-shadow: 0 0 14px #28d2ff, 0 0 30px #28d2ff; }
 .word { left: 40px; top: 50%; transform: translateY(-50%); color: #fff; font-size: 128px; text-shadow: 0 0 30px rgba(40,210,255,.8); }
"""


def render(work: Path, css: str, body: str, badge: str, out: Path) -> None:
    (work / "page.html").write_text(BASE.format(css=css, body=body, badge=html.escape(badge)), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        page.goto((work / "page.html").as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(work / "shot.png"))
        browser.close()
    Image.open(work / "shot.png").convert("RGB").save(out, quality=92)
    print("made", out)


def main() -> None:
    meta = Path(sys.argv[1])
    job = meta.parent
    spec = json.loads((meta / "thumb.json").read_text(encoding="utf-8"))
    media = requests.post("https://graphql.anilist.co", json={"query": QUERY, "variables": {"s": spec["series"]}},
                          timeout=30).json()["data"]["Media"]
    art = fetch(media["coverImage"]["extraLarge"])
    if spec.get("crop"):
        x0, y0, x1, y1 = spec["crop"]
        art = art.crop((round(art.width * x0), round(art.height * y0), round(art.width * x1), round(art.height * y1)))
    sessions: dict = {}
    word, badge = html.escape(spec["word"].upper()), spec["badge"]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        seconds = (spec.get("panels") or [0, 0]) + [0]
        for i, sec in enumerate(seconds[:2]):
            panel(job / "source.mp4", sec, work / f"p{i}.png")
            shutil.copy(work / f"p{i}.png", meta / f"panel-{i}.png")  # kept to check the picks
        cutout(art, sessions).save(work / "hero.png")
        foes = [cutout(fetch(n["image"]["large"]), sessions) for n in media["characters"]["nodes"][1:3]]
        for i, foe in enumerate(foes):
            foe.save(work / f"foe{i}.png")

        sx, sy = spec.get("spot", [0.5, 0.4])
        render(work, SHOCK_CSS, f"""<div class="a bg"></div><img class="a pic" src="p0.png"><div class="a red"></div>
            <div class="a ring" style="left:{380 + int(sx * 900) - 115}px;top:{int(sy * 720) - 115}px"></div>
            <img class="a hero" src="hero.png"><div class="a word">{word}</div>""", badge, job / "thumb-shock.jpg")
        render(work, VERSUS_CSS, f"""<div class="a left"></div><div class="a right"><img src="p0.png"></div><div class="a tear"></div>
            <img class="a hero" src="hero.png"><div class="a word">{word}</div>""", badge, job / "thumb-versus.jpg")
        foe_html = "".join(f'<img class="a foe" src="foe{i}.png" style="left:{x}px">' for i, x in zip(range(len(foes)), (430, 1010)))
        ups = "".join(f'<div class="a up" style="left:{x}px;top:{y}px">&#8963;</div>' for x, y in ((560, 260), (1150, 300), (620, 470), (1100, 500)))
        render(work, POWER_CSS, f"""<div class="a bg"></div>{foe_html}<img class="a ghost" src="hero.png"><div class="a tone"></div>
            <img class="a hero" src="hero.png"><div class="a status"><div class="bar" style="width:84%"></div><div class="bar" style="width:58%"></div>
            <div class="bar" style="width:93%"></div></div>{ups}<div class="a word">{word}</div>""", badge, job / "thumb-power.jpg")


if __name__ == "__main__":
    main()
