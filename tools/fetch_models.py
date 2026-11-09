"""Download every speech model a build may use, so the runner cache can keep them: python fetch_models.py
Runs in the build's uv environment. Each model is optional; one failing never stops the others."""
import urllib.request
from pathlib import Path

KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_DIR = Path("voice-test/models")


def kokoro() -> None:
    KOKORO_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        if not (KOKORO_DIR / name).exists():  # via .part, so a cut-off download is never cached as the real file
            part = KOKORO_DIR / (name + ".part")
            urllib.request.urlretrieve(KOKORO_URL + name, part)
            part.rename(KOKORO_DIR / name)


def whisper() -> None:
    from faster_whisper import WhisperModel

    WhisperModel("small.en", device="cpu", compute_type="int8")


def supertonic() -> None:
    from supertonic import TTS

    TTS(auto_download=True)


for step in (kokoro, whisper, supertonic):
    try:
        step()
        print("model ok:", step.__name__)
    except Exception as exc:
        print("model failed:", step.__name__, type(exc).__name__, str(exc)[:200])
