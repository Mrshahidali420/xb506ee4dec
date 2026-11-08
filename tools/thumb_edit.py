"""Thumbnail for a manhwa long video, made by photo editing (no AI painting). Runs on GitHub (thumb.yml).

The look follows Shahid's brief (8 Nov 2026): the hero in the centre-right, a huge glowing spectral copy of him behind
(his hidden power), a blue RPG status window and floating level-up arrows, enemies looking down from the dark,
cyan rim light against a deep shadow, the left third kept for the words, and a small chapter badge.

How: rembg with the anime model (isnet-anime, from SkyTNT/anime-segmentation) cuts the characters out of the
official AniList art, then the layers are put together in HTML/CSS (glows, blend modes, gradients) and
Chromium (Playwright) takes a 1280x720 screenshot.

Usage: python thumb_edit.py <meta dir>
Reads <meta dir>/thumb.json {"series": "name for AniList", "labels": ["2-3 lines"], "badge": "CH 26-30",
                             "hero": index of the AniList character to use as the hero (optional; default = the cover),
                             "crop": [x0,y0,x1,y1] fractions of the cover (optional)}
Writes <meta dir>/thumbnail.jpg.
"""
from __future__ import annotations

import html
import io
import json
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image
from playwright.sync_api import sync_playwright
from rembg import new_session, remove

W, H = 1280, 720
QUERY = ("query($s:String){Media(search:$s,type:MANGA){bannerImage coverImage{extraLarge} "
         "characters(sort:ROLE,perPage:4){nodes{image{large}}}}}")


def fetch(url: str) -> Image.Image:
    return Image.open(io.BytesIO(requests.get(url, timeout=30).content)).convert("RGB")


def cutout(img: Image.Image, session) -> Image.Image:
    cut = remove(img, session=session).convert("RGBA")
    return cut.crop(cut.getbbox() or (0, 0, cut.width, cut.height))


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&display=block" rel="stylesheet">
<style>
 * {{ margin: 0; box-sizing: border-box; }}
 body {{ width: {W}px; height: {H}px; overflow: hidden; background: #02060c; position: relative; font-family: Anton, Impact, sans-serif; }}
 .abs {{ position: absolute; }}
 .bg {{ inset: -30px; background: url(bg.png) center/cover; filter: blur(9px) brightness(.32) saturate(1.3); }}
 .tone {{ inset: 0; background:
     radial-gradient(ellipse 45% 70% at 66% 55%, rgba(40,210,255,.32), transparent 70%),
     linear-gradient(90deg, rgba(0,0,0,.92) 0%, rgba(0,0,0,.75) 28%, transparent 48%),
     radial-gradient(ellipse at 50% 50%, transparent 55%, rgba(0,0,0,.8) 100%); }}
 .foe {{ top: 0; height: 300px; filter: brightness(.38) sepia(1) hue-rotate(-35deg) saturate(4) drop-shadow(0 0 10px rgba(255,40,60,.7)); }}
 .ghost {{ height: 1060px; top: -200px; left: 840px; transform: translateX(-50%); opacity: .5; mix-blend-mode: screen;
          filter: grayscale(1) brightness(1.5) sepia(1) hue-rotate(150deg) saturate(5) blur(1px) drop-shadow(0 0 40px #28d2ff); }}
 .hero {{ height: 660px; bottom: -6px; left: 850px; transform: translateX(-50%);
         filter: contrast(1.12) saturate(1.25) drop-shadow(0 0 2px #bff) drop-shadow(0 0 16px #28d2ff) drop-shadow(0 0 40px rgba(40,210,255,.6)); }}
 .status {{ left: 1015px; top: 92px; width: 228px; height: 148px; border: 3px solid #28d2ff; border-radius: 12px;
           background: rgba(8,50,110,.55); box-shadow: 0 0 22px #28d2ff, inset 0 0 18px rgba(40,210,255,.4); padding: 40px 16px 0; }}
 .status:before {{ content: ""; position: absolute; left: 12px; right: 12px; top: 26px; height: 2px; background: #28d2ff; }}
 .bar {{ height: 13px; border-radius: 7px; background: linear-gradient(90deg, #28d2ff, #9ff); margin: 0 0 17px; box-shadow: 0 0 8px #28d2ff; }}
 .up {{ width: 0; height: 0; filter: drop-shadow(0 0 8px #28d2ff); }}
 .up svg {{ overflow: visible; }}
 .words {{ left: 42px; top: 50%; transform: translateY(-50%); width: 520px; }}
 .words div {{ line-height: 1.02; -webkit-text-stroke: 8px #000; paint-order: stroke fill; text-shadow: 0 6px 18px rgba(0,0,0,.9); letter-spacing: 1px; }}
 .badge {{ right: 40px; bottom: 34px; background: #ffd600; color: #000; font-size: 34px; padding: 4px 13px 2px;
          border: 4px solid #000; border-radius: 8px; }}
</style></head><body>
<div class="abs bg"></div>
{foes}
<img class="abs ghost" src="hero.png">
<div class="abs tone"></div>
<img class="abs hero" src="hero.png">
<div class="abs status"><div class="bar" style="width:84%"></div><div class="bar" style="width:58%"></div><div class="bar" style="width:93%"></div></div>
{arrows}
<div class="abs words">{words}</div>
<div class="abs badge">{badge}</div>
</body></html>"""

ARROW = ('<div class="abs up" style="left:{x}px;top:{y}px"><svg width="1" height="1">'
         '<polyline points="-{s},{s} 0,0 {s},{s}" fill="none" stroke="#7ef" stroke-width="{w}" stroke-linecap="round"/>'
         '<polyline points="-{s},{t} 0,{h} {s},{t}" fill="none" stroke="#7ef" stroke-width="{w}" stroke-linecap="round"/></svg></div>')


def build_page(spec: dict, foes: int) -> str:
    sizes = [118 if len(t) <= 9 else 96 if len(t) <= 13 else 78 for t in spec["labels"]]
    colors = ["#ffd600", "#ffffff", "#ffd600"]
    words = "".join(f'<div style="font-size:{s}px;color:{c}">{html.escape(t)}</div>'
                    for t, s, c in zip(spec["labels"], sizes, colors))
    foe_html = "".join(f'<img class="abs foe" src="foe{i}.png" style="left:{x}px">'
                       for i, x in zip(range(foes), (470, 1030)))
    arrows = "".join(ARROW.format(x=x, y=y, s=s, h=s // 2, t=s + s // 2, w=max(5, s // 5))
                     for x, y, s in ((640, 300, 26), (1190, 360, 22), (700, 520, 18), (1130, 560, 20)))
    return PAGE.format(W=W, H=H, foes=foe_html, arrows=arrows, words=words, badge=html.escape(spec["badge"]))


def main() -> None:
    meta = Path(sys.argv[1])
    spec = json.loads((meta / "thumb.json").read_text(encoding="utf-8"))
    media = requests.post("https://graphql.anilist.co", json={"query": QUERY, "variables": {"s": spec["series"]}},
                          timeout=30).json()["data"]["Media"]
    art = fetch(media["coverImage"]["extraLarge"])
    if spec.get("crop"):
        x0, y0, x1, y1 = spec["crop"]
        art = art.crop((round(art.width * x0), round(art.height * y0), round(art.width * x1), round(art.height * y1)))
    people = [fetch(n["image"]["large"]) for n in media["characters"]["nodes"]]
    hero_src = people[spec["hero"]] if isinstance(spec.get("hero"), int) else art
    session = new_session("isnet-anime")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (fetch(media["bannerImage"]) if media.get("bannerImage") else art).save(work / "bg.png")
        cutout(hero_src, session).save(work / "hero.png")
        side = [p for i, p in enumerate(people) if i != spec.get("hero", -1)][1:3]
        for i, person in enumerate(side):
            cutout(person, session).save(work / f"foe{i}.png")
        (work / "page.html").write_text(build_page(spec, len(side)), encoding="utf-8")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": W, "height": H})
            page.goto((work / "page.html").as_uri(), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(work / "shot.png"))
            browser.close()
        Image.open(work / "shot.png").convert("RGB").save(meta / "thumbnail.jpg", quality=92)
    print("thumbnail:", meta / "thumbnail.jpg")


if __name__ == "__main__":
    main()
