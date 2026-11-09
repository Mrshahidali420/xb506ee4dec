"""VISUAL test: 5 panels -> one 1080x1920 clip each -> one joined mp4 per tool.
python visual.py ffmpeg|kage|acmp|depthflow|narrator PANEL_DIR [REPO_DIR]"""
import subprocess
import sys
import time
from pathlib import Path

from common import pkg, run_tool

W, H, FPS, SECS = 1080, 1920, 30, 4
tool, panel_dir = sys.argv[1], Path(sys.argv[2])
repo = Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else None
PANELS = [panel_dir.resolve() / f"panel{i}.png" for i in range(1, 6)]


def join(clips, out):
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)], check=True)
    lst.unlink()
    for c in clips:
        c.unlink()


def per_panel(d, name, render):
    clips, times = [], []
    for i, p in enumerate(PANELS, 1):
        t0 = time.time()
        c = d / f"{name}_c{i}.mp4"
        render(p, c)
        times.append(round(time.time() - t0, 1))
        clips.append(c)
    join(clips, d / f"{name}.mp4")
    return [f"{name}.mp4"], f"per-clip seconds {times}"


def ffmpeg_zoom(d):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import manhwa_panels as mp  # our current production panel_clip (3 s clips)
    return per_panel(d, "ffmpeg-zoom", lambda p, c: mp.panel_clip(p, c))


def pipe_frames(frames, out):
    pr = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                           "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(out)],
                          stdin=subprocess.PIPE)
    for f in frames:
        pr.stdin.write(f.tobytes())
    pr.stdin.close()
    if pr.wait():
        raise RuntimeError("ffmpeg failed")


def kage(d):
    sys.path.insert(0, str(repo))
    from backend.visual_engine import ShotRenderer

    def render(p, c):
        r = ShotRenderer(image_path=p, out_width=W, out_height=H, camera_motion="slow_zoom_in", fps=FPS, parallax=0.6, drift=0.3)
        pipe_frames(r.iter_frames(SECS), c)
    return per_panel(d, "kage", render)


def acmp(d):
    import numpy as np
    from PIL import Image
    from acmp.animation.engine import animate_panel
    from acmp.config import AnimationConfig

    def render(p, c):
        img = Image.open(p).convert("RGB")
        kind = "ken_burns_pan" if img.height > img.width else "ken_burns_zoom"
        frames = animate_panel(img, kind, (W, H), AnimationConfig(seconds_per_panel=SECS))
        pipe_frames((np.asarray(f.convert("RGB").resize((W, H))) for f in frames), c)
    return per_panel(d, "acmp", render)


SCENE = """
import math, sys
from attrs import define
from depthflow.scene import DepthScene

@define
class Scene(DepthScene):
    def update(self):
        amp = AMP
        self.state.isometric = 0.55
        self.state.steady = 0.40
        self.state.offset = (amp * math.sin(self.cycle), 0.4 * amp * math.sin(self.cycle * 0.5))
        self.state.height = 0.06 * (0.5 - 0.5 * math.cos(self.cycle))

s = Scene(backend="headless")
s.ffmpeg.h264(preset="fast")
s.input(image=sys.argv[1])
s.main(output=sys.argv[2], time=SECS, fps=FPS, width=W, height=H)
"""


def depthflow_like(d, name, amp, pad):
    from PIL import Image
    run_py = d / f"_{name}.py"
    run_py.write_text(SCENE.replace("AMP", str(amp)).replace("SECS", str(SECS)).replace("FPS", str(FPS))
                      .replace("=W,", f"={W},").replace("=H)", f"={H})"))

    def render(p, c):
        src = p
        if pad:  # comic-manga-narrator fits + pads the panel to the target size first
            im = Image.open(p).convert("RGB")
            s = min(W / im.width, H / im.height)
            im = im.resize((int(im.width * s), int(im.height * s)))
            bg = Image.new("RGB", (W, H))
            bg.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
            src = d / "_pad.png"
            bg.save(src)
        r = subprocess.run(["xvfb-run", "-a", sys.executable, str(run_py), str(src), str(c)], capture_output=True, text=True, timeout=900)
        if r.returncode or not c.exists():
            raise RuntimeError(f"exit {r.returncode}\n{(r.stdout + r.stderr)[-900:]}")
    out = per_panel(d, name, render)
    run_py.unlink()
    (d / "_pad.png").unlink(missing_ok=True)
    return out


def depthflow(d):
    return depthflow_like(d, "depthflow", 0.45, False)


def narrator(d):
    # the narrator's own render_panel.py preset (GentleDepth: amp 0.15, anchored) on its fit+padded panel
    return depthflow_like(d, "comic-manga-narrator", 0.15, True)


TOOLS = {
    "ffmpeg": ("current ffmpeg zoom", ffmpeg_zoom, lambda: "tools/manhwa_panels.py panel_clip"),
    "kage": ("Kage", kage, lambda: "Kage ShotRenderer with depth parallax and drift; " + pkg("torch", "transformers")),
    "acmp": ("acmp", acmp, lambda: "acmp Ken Burns, no AI, no depth; " + pkg("acmp")),
    "depthflow": ("DepthFlow", depthflow, lambda: pkg("depthflow", "shaderflow", "torch")),
    "narrator": ("comic-manga-narrator", narrator, lambda: "its DepthFlow GentleDepth preset; " + pkg("depthflow")),
}
if __name__ == "__main__":
    name, fn, pin = TOOLS[tool]
    run_tool("visual", name, fn, pin)
