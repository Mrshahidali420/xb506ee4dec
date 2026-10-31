"""Cut webtoon strips into panels and render them as one 9:16 'source' video for build_short.py.

Usage: python manhwa_panels.py <project_dir>
Reads <project_dir>/pages/*.jpg|png (in name order), writes:
  panels/p_###.png      one image per panel
  source.mp4            each panel = SECONDS of full-screen motion (tall: pan down, wide: slow zoom)
  work/shots.txt        one line per panel, so clips never cross into the next panel
  work/panels_text.json [{"panel": n, "t": n*3, "text": "..."}] read by the script writer (needs rapidocr-onnxruntime)
  work/panels_sheet_*.jpg  labelled contact sheets for picking panels
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
WIDTH = 800          # strips are normalised to this width before cutting
MIN_GAP = 24         # rows of gap needed to split two panels
MIN_PANEL = 140      # smaller pieces are joined to their neighbour
SECONDS = 3.0
BUBBLE_PANEL = 0.6    # a panel this much white bubble area (a bubble-only strip) keeps its words
OCR_SLICE = 1400     # tall panels are read in slices of this many rows, the reader shrinks big images
OCR_MIN_SCORE = 0.5
LETTER = 45         # a dark mark this tall or less (and up to 4x as wide) inside white counts as lettering
W, H, FPS = 1080, 1920, 30


def load_strip(pages: list[Path]) -> np.ndarray:
    rows = []
    for page in pages:
        img = Image.open(page).convert("RGB")
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)))
        rows.append(np.asarray(img))
    return np.concatenate(rows, axis=0)


def cut_panels(strip: np.ndarray) -> list[tuple[int, int]]:
    # a row is gap when under 1.5% of its pixels differ from the row's own background colour,
    # so stray sparks and JPEG noise on a black or white gutter do not count as art
    bg = np.median(strip, axis=1, keepdims=True)
    differs = np.abs(strip.astype(np.int16) - bg.astype(np.int16)).max(axis=2) > 40
    blank = differs.mean(axis=1) < 0.015
    spans, start, gap = [], None, 0
    for y, is_blank in enumerate(blank):
        if not is_blank:
            if start is None:
                start = y
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= MIN_GAP:
                spans.append((start, y - gap + 1))
                start, gap = None, 0
    if start is not None:
        spans.append((start, len(blank)))
    merged: list[tuple[int, int]] = []
    for a, b in spans:  # join slivers (sound effects, thin borders) onto the panel above
        if merged and b - a < MIN_PANEL:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= MIN_PANEL]


def blank_bubbles(panel: np.ndarray) -> np.ndarray:
    """Erase the lettering inside speech bubbles and on white captions; the bubble shapes stay.

    Letters are small dark marks sitting inside a white area. Long marks (bubble outlines,
    drawn lines) and big ones (sound effects) are art, so they are kept.
    """
    from scipy import ndimage

    white = (panel > 225).all(axis=2)
    filled = ndimage.binary_closing(white, structure=np.ones((15, 15)), iterations=2)
    regions, _ = ndimage.label(filled)
    sizes = np.bincount(regions.ravel())
    big = np.isin(regions, np.nonzero(sizes > 2500)[0][1:])  # white areas worth reading text from
    if big.mean() > BUBBLE_PANEL:  # the bubble is the panel: blanked, nothing is left to look at, so keep the words
        return panel
    marks = big & ~white
    labels, n = ndimage.label(marks)
    out = panel.copy()
    for k, box in enumerate(ndimage.find_objects(labels), start=1):
        if box and box[0].stop - box[0].start <= LETTER and box[1].stop - box[1].start <= 4 * LETTER:
            out[box][labels[box] == k] = 255
    return out


def ocr_panels(pdir: Path, count: int, work: Path) -> None:
    """Read the words on every panel into work/panels_text.json. Optional: no OCR package, no file."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
    except Exception as exc:
        print(f"OCR skipped: {exc}", flush=True)
        return
    out = []
    for i in range(count):
        img = np.asarray(Image.open(pdir / f"p_{i:03d}.png").convert("RGB"))
        words = []
        for y in range(0, img.shape[0], OCR_SLICE):  # slices come top to bottom, so the text stays in reading order
            found, _ = engine(img[y:y + OCR_SLICE])
            lines = sorted((b[0][1], b[0][0], t) for b, t, score in (found or []) if float(score) >= OCR_MIN_SCORE)
            words += [t for _, _, t in lines]
        out.append({"panel": i, "t": round(i * SECONDS), "text": " ".join(words)})
    (work / "panels_text.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"OCR: {sum(1 for o in out if o['text'])}/{count} panels have text", flush=True)


def panel_clip(img: Path, out: Path) -> None:
    w, h = Image.open(img).size
    frames = int(SECONDS * FPS)
    if h / w > H / W:  # tall panel: fill the width, pan from top to bottom
        vf = (f"scale={W}:-2,crop={W}:{H}:0:'(ih-{H})*t/{SECONDS}',setsar=1,fps={FPS}")
        args = ["-loop", "1", "-t", f"{SECONDS}", "-i", str(img), "-vf", vf]
    else:  # wide panel: blurred fill behind, the panel at full width, slow push-in
        vf = (f"[0:v]split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=30:2,eq=brightness=-0.15[bg];"
              f"[b]scale={W * 2}:-2,zoompan=z='1+0.08*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
              f"d={frames}:s={W}x{round(h * W / w) // 2 * 2}:fps={FPS}[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}")
        args = ["-loop", "1", "-t", f"{SECONDS}", "-i", str(img), "-filter_complex", vf]
    subprocess.run([FFMPEG, "-v", "error", "-y", *args, "-frames:v", str(frames), "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)], check=True)


def main() -> None:
    proj = Path(sys.argv[1]).resolve()
    keep_text = "--blank-text" not in sys.argv  # off by default: the cleaner also wiped titles and narration art
    pages = sorted(p for p in (proj / "pages").iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    pdir, work, clips = proj / "panels", proj / "work", proj / "work" / "panel_clips"
    for d in (pdir, work, clips):
        d.mkdir(parents=True, exist_ok=True)
    # one episode at a time ("e03_012.jpg" -> "e03"), so memory holds one episode, not the whole series
    episodes: dict[str, list[Path]] = {}
    for page in pages:
        episodes.setdefault(page.name.split("_")[0], []).append(page)
    count = 0
    for name, group in episodes.items():
        strip = load_strip(group)
        for a, b in cut_panels(strip):
            panel = strip[a:b] if keep_text else blank_bubbles(strip[a:b])
            Image.fromarray(panel).save(pdir / f"p_{count:03d}.png")
            count += 1
        del strip
        print(f"{name}: {len(group)} strips, {count} panels so far", flush=True)
    ocr_panels(pdir, count, work)
    spans = range(count)
    listing = []
    for i in spans:
        clip = clips / f"c_{i:03d}.mp4"
        panel_clip(pdir / f"p_{i:03d}.png", clip)
        listing.append(f"file '{clip.as_posix()}'")
    (work / "panels.txt").write_text("\n".join(listing) + "\n", encoding="utf-8")
    # silent track so the builder's audio mapping works like a film source
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "panels.txt"),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest", "-c:v", "copy",
                    "-c:a", "aac", str(proj / "source.mp4")], check=True)
    (work / "shots.txt").write_text(
        "\n".join(f"{i * SECONDS:.2f} {(i + 1) * SECONDS:.2f}" for i in range(len(spans))), encoding="utf-8")
    for k in range(0, len(spans), 24):  # labelled sheets: panel number = start second / SECONDS
        thumbs = []
        for i in range(k, min(k + 24, len(spans))):
            im = Image.open(pdir / f"p_{i:03d}.png")
            im.thumbnail((260, 420))
            tile = Image.new("RGB", (260, 440), "black")
            tile.paste(im, ((260 - im.width) // 2, 20))
            from PIL import ImageDraw
            ImageDraw.Draw(tile).text((4, 2), f"#{i} t={i * SECONDS:.0f}", fill="yellow")
            thumbs.append(tile)
        sheet = Image.new("RGB", (260 * 8, 440 * 3), "black")
        for j, t in enumerate(thumbs):
            sheet.paste(t, (260 * (j % 8), 440 * (j // 8)))
        sheet.save(work / f"panels_sheet_{k // 24 + 1}.jpg", quality=85)
    print(f"{len(pages)} strips -> {len(spans)} panels, source.mp4 = {len(spans) * SECONDS:.0f}s")


if __name__ == "__main__":
    main()
