"""VOICE test: one tool per run (python voice.py kokoro|pocket|supertonic|chatterbox). Same one-line text for all."""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from common import pkg, run_tool

TEXT = (Path(__file__).parent / "text.txt").read_text(encoding="utf8").strip()


def mp3(wav: Path, out: Path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ar", "44100", "-b:a", "192k", str(out)], check=True)
    wav.unlink()


def kokoro(d):
    import soundfile as sf
    from kokoro_onnx import Kokoro
    models = Path("voice-test/models")  # same folder + cache key as build.yml
    models.mkdir(parents=True, exist_ok=True)
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    for n in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        if not (models / n).exists():
            urllib.request.urlretrieve(base + n, models / n)
    k = Kokoro(str(models / "kokoro-v1.0.onnx"), str(models / "voices-v1.0.bin"))
    s, r = k.create(TEXT, voice="am_liam", speed=1.0, lang="en-us")
    sf.write(d / "kokoro.wav", s, r)
    mp3(d / "kokoro.wav", d / "kokoro_am_liam.mp3")
    return ["kokoro_am_liam.mp3"], "voice am_liam"


def pocket(d):
    import scipy.io.wavfile
    from pocket_tts import TTSModel
    t0 = time.time()
    m = TTSModel.load_model()
    files = []
    for voice in ("marius", "javert"):  # built-in male English voices from the README catalog
        st = m.get_state_for_audio_prompt(voice)
        a = m.generate_audio(st, TEXT)
        scipy.io.wavfile.write(d / f"{voice}.wav", m.sample_rate, a.numpy())
        mp3(d / f"{voice}.wav", d / f"pocket_{voice}.mp3")
        files.append(f"pocket_{voice}.mp3")
    return files, f"voices marius + javert, both incl. model load ({time.time() - t0:.0f}s)"


def supertonic(d):
    from supertonic import TTS
    tts = TTS(auto_download=True)
    files = []
    for v in ("M1", "M2", "M3", "M4", "M5"):  # all five built-in male voices, so the owner can pick
        wav, _ = tts.synthesize(text=TEXT, voice_style=tts.get_voice_style(voice_name=v), total_steps=8, speed=1.0, lang="en")
        tts.save_audio(wav, str(d / f"{v}.wav"))
        mp3(d / f"{v}.wav", d / f"supertonic_{v}.mp3")
        files.append(f"supertonic_{v}.mp3")
    return files, "male voices M1-M5, 8 steps"


def chatterbox(d):
    import torchaudio as ta
    from chatterbox.tts import ChatterboxTTS
    m = ChatterboxTTS.from_pretrained(device="cpu")
    t0 = time.time()
    wav = m.generate(TEXT)  # built-in default voice, no reference clip
    gen = time.time() - t0
    ta.save(str(d / "cb.wav"), wav, m.sr)
    mp3(d / "cb.wav", d / "chatterbox_default.mp3")
    return ["chatterbox_default.mp3"], f"generate() alone {gen:.0f}s, the rest is model load"


TOOLS = {
    "kokoro": ("Kokoro", kokoro, lambda: pkg("kokoro-onnx", "onnxruntime")),
    "pocket": ("pocket-tts", pocket, lambda: pkg("pocket-tts", "torch")),
    "supertonic": ("Supertonic", supertonic, lambda: pkg("supertonic", "onnxruntime")),
    "chatterbox": ("Chatterbox", chatterbox, lambda: pkg("chatterbox-tts", "torch")),
}
if __name__ == "__main__":
    name, fn, pin = TOOLS[sys.argv[1]]
    run_tool("voice", name, fn, pin)
