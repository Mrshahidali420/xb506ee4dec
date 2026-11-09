"""One DepthFlow render: python depth_scene.py IMAGE OUT SECONDS FPS WIDTH HEIGHT
Runs in its own environment (torch + depthflow), started by build_short.py under xvfb-run."""
import math
import sys

from attrs import define
from depthflow.scene import DepthScene

AMP = 0.45  # the strength the lab tested


@define
class Scene(DepthScene):
    def update(self):
        self.state.isometric = 0.55
        self.state.steady = 0.40
        self.state.offset = (AMP * math.sin(self.cycle), 0.4 * AMP * math.sin(self.cycle * 0.5))
        self.state.height = 0.06 * (0.5 - 0.5 * math.cos(self.cycle))


image, out, secs, fps, width, height = sys.argv[1:7]
scene = Scene(backend="headless")
scene.ffmpeg.h264(preset="fast")
scene.input(image=image)
scene.main(output=out, time=float(secs), fps=int(fps), width=int(width), height=int(height))
