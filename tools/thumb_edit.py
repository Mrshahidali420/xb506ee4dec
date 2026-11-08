"""Thumbnails for a manhwa long video, made by photo editing (no AI painting). Runs on GitHub (thumb.yml).

Styles copy what the top manhwa recap videos do (YouTube study, 8 Nov 2026: 16 thumbnails with 1-4.5M views):
one bright full-bleed scene, small yellow role labels ("MC", "ENEMY", "MAX LEVEL") with curved arrows pointing at
the people, strong emotion, very few words. Ours use the part's real panels instead of painted art.

  scene   one panel fills the frame; 1-3 labels with arrows
  split   two panels side by side (the MC | the threat), a slanted cut between; labels on each
  shock   the most shocking panel blown up, red danger tone, one huge word, optional hero cut out of a panel

Usage: python thumb_edit.py <meta dir>
<meta dir>/thumb.json:
  {"badge": "CH 26-30",
   "thumbs": [
     {"name": "scene", "style": "scene", "panel": 1389, "focus": [0.6, 0.4],   ("fit": "contain" shows a tall panel whole,
                                                                          "sides": [left second, right second] fill the sides)
      "labels": [{"text": "MC?", "at": [0.08, 0.12], "to": [0.55, 0.3]}]},
     {"name": "split", "style": "split", "panels": [1359, 1374], "focus": [[0.5, 0.4], [0.5, 0.3]], "labels": [...]},
     {"name": "shock", "style": "shock", "panel": 1389, "word": "KILL ME", "hero": 1359}
   ]}
  panel = a second in the part's source.mp4 (pick it from the panel sheets); focus = which point of the panel stays in view;
  label "at" = where the label sits, "to" = where its arrow points, both as fractions of the 1280x720 frame.
The job folder is the parent of <meta dir>. Writes thumb-<name>.jpg there and panel-<second>.png into <meta dir>.
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

W, H = 1280, 720
PANEL_SECONDS = 3
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


def panel(video: Path, second: float, to: Path) -> str:
    """The middle frame of the panel shown at `second`, with the video's black letterbox cut off."""
    mid = int(second // PANEL_SECONDS) * PANEL_SECONDS + PANEL_SECONDS / 2
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{mid:.2f}", "-i", str(video), "-frames:v", "1", str(to)], check=True)
    img = Image.open(to).convert("RGB")
    box = img.convert("L").point(lambda v: 255 if v > 22 else 0).getbbox()
    if box and box[2] - box[0] > img.width // 3:
        img = img.crop(box)
    img.save(to)
    return to.name


def hero_cut(src: Path, to: Path) -> str | None:
    """The hero cut out of a panel (rembg: BiRefNet, then the anime model). None when no model finds a clean figure:
    a cut that keeps the whole panel looks like a pasted box, so the thumbnail goes without a hero."""
    from rembg import new_session, remove

    img = Image.open(src).convert("RGB")
    for name in ("birefnet-general", "isnet-anime"):
        cut = remove(img, session=new_session(name), post_process_mask=True).convert("RGBA")
        kept = sum(cut.getchannel("A").point(lambda v: 1 if v > 128 else 0).getdata()) / (img.width * img.height)
        if 0.08 < kept < 0.8:
            break
    else:
        return None
    cut.crop(cut.getbbox() or (0, 0, cut.width, cut.height)).save(to)
    return to.name


BASE = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Luckiest+Guy&display=block" rel="stylesheet">
<style>
 * {{ margin: 0; box-sizing: border-box; }}
 body {{ width: 1280px; height: 720px; overflow: hidden; background: #000; position: relative; }}
 .a {{ position: absolute; }}
 .full {{ inset: 0; width: 1280px; height: 720px; object-fit: cover; filter: contrast(1.15) saturate(1.35) brightness(1.06); }}
 .label {{ font-family: "Luckiest Guy", Anton, sans-serif; color: #ffd600; font-size: 82px; line-height: 1; white-space: nowrap;
          -webkit-text-stroke: 9px #000; paint-order: stroke fill; transform: translate(-50%, -50%) rotate(-3deg);
          filter: drop-shadow(0 4px 6px rgba(0,0,0,.6)); }}
 .word {{ font-family: Anton, sans-serif; white-space: nowrap; line-height: .95; -webkit-text-stroke: 10px #000; paint-order: stroke fill; }}
 .badge {{ right: 26px; top: 22px; background: #ffd600; color: #000; font: 28px Anton, sans-serif; padding: 3px 10px 1px;
          border: 4px solid #000; border-radius: 8px; }}
 svg.arrows {{ position: absolute; inset: 0; overflow: visible; filter: drop-shadow(0 3px 4px rgba(0,0,0,.6)); }}
 {css}
</style></head><body>{body}
<svg class="arrows" width="1280" height="720"><defs>
 <marker id="tip" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="3.2" markerHeight="3.2" orient="auto-start-reverse">
  <path d="M0,0 L10,5 L0,10 z" fill="#ffd600" stroke="#000" stroke-width="1.6"/></marker></defs>{arrows}</svg>
<div class="a badge">{badge}</div></body></html>"""

SPLIT_CSS = """
 .half { top: 0; height: 720px; width: 700px; overflow: hidden; }
 .half img { width: 100%; height: 100%; object-fit: cover; filter: contrast(1.15) saturate(1.35) brightness(1.06); }
 .l { left: 0; clip-path: polygon(0 0, 100% 0, 88% 100%, 0 100%); }
 .r { right: 0; clip-path: polygon(12% 0, 100% 0, 100% 100%, 0 100%); }
 .cut { left: 618px; top: -20px; width: 14px; height: 780px; background: #fff; transform: skewX(-6.3deg); box-shadow: 0 0 18px #fff; }
"""

SHOCK_CSS = """
 .bg { inset: -40px; filter: blur(14px) brightness(.35) saturate(1.4); }
 .pic { right: 0; top: 0; height: 720px; width: 920px; object-fit: cover; filter: contrast(1.25) saturate(1.35) brightness(1.05);
        -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 22%); }
 .red { inset: 0; background: radial-gradient(ellipse 60% 80% at 70% 50%, transparent 40%, rgba(120,0,0,.55) 100%),
        linear-gradient(90deg, rgba(0,0,0,.95) 0%, rgba(0,0,0,.6) 30%, transparent 45%); }
 .hero { left: 0; bottom: -10px; height: 480px; filter: drop-shadow(0 0 3px #fff) drop-shadow(0 0 22px rgba(255,40,40,.8)); }
 .word { left: 36px; top: 46px; color: #fff; font-size: 150px; text-shadow: 0 0 30px rgba(255,0,0,.7); }
"""


def pos(f: list[float]) -> str:
    return f"object-position: {f[0] * 100:.0f}% {f[1] * 100:.0f}%"


def labels(items: list[dict]) -> tuple[str, str]:
    """Label text and its curved arrow (an SVG quadratic curve, bent away from the straight line)."""
    boxes, arrows = [], []
    for it in items:
        ax, ay = it["at"][0] * W, it["at"][1] * H
        boxes.append(f'<div class="a label" style="left:{ax:.0f}px;top:{ay:.0f}px">{html.escape(it["text"])}</div>')
        if "to" in it:
            tx, ty = it["to"][0] * W, it["to"][1] * H
            sx, sy = ax + (tx - ax) * 0.22, ay + (ty - ay) * 0.22  # start just outside the text
            cx, cy = (sx + tx) / 2 - (ty - sy) * 0.3, (sy + ty) / 2 + (tx - sx) * 0.3
            for color, width in (("#000", 17), ("#ffd600", 9)):
                end = ' marker-end="url(#tip)"' if color != "#000" else ""
                arrows.append(f'<path d="M{sx:.0f},{sy:.0f} Q{cx:.0f},{cy:.0f} {tx:.0f},{ty:.0f}" fill="none" stroke="{color}" '
                              f'stroke-width="{width}" stroke-linecap="round"{end}/>')
    return "".join(boxes), "".join(arrows)


def make(work: Path, job: Path, t: dict) -> tuple[str, str, str]:
    video = job / "source.mp4"
    boxes, arrows = labels(t.get("labels", []))
    if t["style"] == "scene":
        p = panel(video, t["panel"], work / f"p{t['panel']}.png")
        if t.get("fit") == "contain":  # a tall panel shown whole in the middle, two more panels of the part fill the sides
            left, right = (panel(video, s, work / f"p{s}.png") for s in t.get("sides", [t["panel"] - 3, t["panel"] + 3]))
            side = "top:0;height:720px;width:470px;object-fit:cover;filter: contrast(1.1) saturate(1.3) brightness(.8)"
            return "", (f'<img class="a" src="{left}" style="left:0;{side};object-position:{t.get("side_focus", ["50% 40%", "50% 40%"])[0]}">'
                        f'<img class="a" src="{right}" style="right:0;{side};object-position:{t.get("side_focus", ["50% 40%", "50% 40%"])[1]}">'
                        f'<img class="a" src="{p}" style="left:50%;top:0;height:720px;transform:translateX(-50%);'
                        f'border-left:8px solid #fff;border-right:8px solid #fff;'
                        f'filter: contrast(1.15) saturate(1.35) drop-shadow(0 0 30px #000)">{boxes}'), arrows
        return "", f'<img class="a full" src="{p}" style="{pos(t.get("focus", [0.5, 0.4]))}">{boxes}', arrows
    if t["style"] == "split":
        a, b = (panel(video, s, work / f"p{s}.png") for s in t["panels"])
        fa, fb = t.get("focus", [[0.5, 0.4], [0.5, 0.4]])
        return SPLIT_CSS, (f'<div class="a half l"><img src="{a}" style="{pos(fa)}"></div>'
                           f'<div class="a half r"><img src="{b}" style="{pos(fb)}"></div><div class="a cut"></div>{boxes}'), arrows
    p = panel(video, t["panel"], work / f"p{t['panel']}.png")
    hero = ""
    if t.get("hero"):
        src = work / panel(video, t["hero"], work / f"p{t['hero']}.png")
        cut = hero_cut(src, work / "hero.png")
        hero = f'<img class="a hero" src="{cut}">' if cut else ""
    return SHOCK_CSS, (f'<img class="a bg" src="{p}" style="width:1360px;height:800px;object-fit:cover">'
                       f'<img class="a pic" src="{p}" style="{pos(t.get("focus", [0.5, 0.35]))}"><div class="a red"></div>{hero}'
                       f'<div class="a word">{html.escape(t["word"].upper())}</div>{boxes}'), arrows


def main() -> None:
    meta = Path(sys.argv[1])
    job = meta.parent
    spec = json.loads((meta / "thumb.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
        work = Path(tmp)
        browser = pw.chromium.launch()
        for t in spec["thumbs"]:
            css, body, arrows = make(work, job, t)
            (work / "page.html").write_text(BASE.format(css=css, body=body, arrows=arrows, badge=html.escape(spec["badge"])),
                                            encoding="utf-8")
            page = browser.new_page(viewport={"width": W, "height": H})
            page.goto((work / "page.html").as_uri(), wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.screenshot(path=str(work / "shot.png"))
            page.close()
            Image.open(work / "shot.png").convert("RGB").save(job / f"thumb-{t['name']}.jpg", quality=92)
            print("made", job / f"thumb-{t['name']}.jpg")
        browser.close()
        for p in work.glob("p*.png"):  # the panels, kept to check the picks and the focus
            shutil.copy(p, meta / f"panel-{p.stem[1:]}.png")


if __name__ == "__main__":
    main()
