"""Pull the 5 panel frames from the cached panel video and crop the black letterbox."""
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

video, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for i, sec in enumerate((31.5, 91.5, 151.5, 301.5, 451.5), 1):
    raw = out / f"raw{i}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(sec), "-i", video, "-frames:v", "1", str(raw)], check=True)
    im = Image.open(raw).convert("RGB")
    g = np.asarray(im.convert("L"))
    ys, xs = np.where(g > 24)
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else (0, 0, *im.size)
    im.crop(box).save(out / f"panel{i}.png")
    raw.unlink()
    print(f"panel{i}: {im.size} -> {box}")
