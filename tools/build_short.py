"""Build a 9:16 viral recap short from a project folder that holds script.json + source video.

Usage: python build_short.py <project_dir>

script.json segments are either narration ({"say": "...", "clips": [src_seconds, ...]})
or an original line kept from the film ({"orig": [start, end], "text": "..."}).
Words wrapped in *stars* become yellow keyword captions.
A clip is a source second, or [source_second, word_index] to start that shot on a given word.
Clips snap inside one camera shot (work/shots.txt), so a clip never jumps across a cut.
During narration the film sound comes from the no-voice stem ("bed_audio"), so no character talks
under the narrator. "music" is looped under the whole short and ducks when someone speaks.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

import truststore

truststore.inject_into_ssl()

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
W, H, FPS = 1080, 1920, 30  # 9:16 default; "aspect": "16:9" in script.json switches to 1920x1080
FG_HEIGHT = 900
HEADER_Y = 150  # vertical videos: top of the page name card, in the band above the 900 px picture
LOGO_SIZE, LOGO_X, LOGO_Y = 130, 40, 240  # vertical comics: small round page logo instead of the card
WIDE = False
CROP_BOTTOM = 0.0  # share of the source height to cut off (burned-in film subtitles)
FILM_ZOOM = 1.08  # film shots are pushed in a little, so the frame is not a 1:1 copy of the film (fewer Content ID matches)
MUSIC_OUT = 0.4  # seconds the music takes to fade away before the last line (silence makes the cliffhanger hit)
MUSIC_TRIM = {"Shinigami": 4.0}  # extra dB under the voice for tracks that feel loud at the same average level
FONT = ("C\\:/Windows/Fonts/arialbd.ttf" if sys.platform == "win32"
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")  # GitHub runners are Linux
YELLOW = "&H00D7FF&"  # ASS colours are BGR


def run(args: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args[:6])}...\n{result.stderr[-1500:]}")


def duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return float(out.stdout.strip())


def plain(text: str) -> str:
    return text.replace("*", "")


# ---------- voice ----------

KOKORO_DIR = Path(__file__).resolve().parent.parent / "voice-test" / "models"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO = None  # the loaded model, shared by every line


class VoiceEngineDown(Exception):
    """The current voice engine failed; the build restarts on the next engine so all lines match."""


def chatterbox_tts(text: str, ref: Path, raw: Path) -> None:
    from gradio_client import Client, handle_file

    try:
        got = Client("ResembleAI/Chatterbox", verbose=False, token=os.environ.get("HF_TOKEN")).predict(
            text, handle_file(str(ref)), 0.5, 0.8, 0, 0.5, True, api_name="/generate_tts_audio")
    except Exception as exc:
        raise VoiceEngineDown(f"chatterbox: {exc}") from exc
    shutil.copy(got, raw)


def env_key(name: str) -> str:
    """A key from the environment or ~/.config/watch/.env; empty when missing."""
    if os.environ.get(name):
        return os.environ[name]
    env = Path.home() / ".config/watch/.env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    return next((line.split("=", 1)[1].strip().strip('"') for line in lines if line.startswith(name + "=")), "")


def groq_key() -> str:
    return env_key("GROQ_API_KEY")


ELEVEN_VOICES = {"liam": "TX3LPaxmHKxFdv7VOQHJ"}  # Liam = closest match to the viral narrator (0.73)


# free clone voices: a clean reference clip + its exact words. Liam copy scores 0.78 vs the viral narrator.
F5_VOICES = {"liam": (KOKORO_DIR.parent / "liam-ref.wav",
                      "Life isn't about finding yourself. Life is about creating yourself.")}


def f5_tts(text: str, voice: str, raw: Path) -> None:
    """Free F5-TTS voice clone on the Hugging Face cloud (needs a free HF_TOKEN for daily quota)."""
    from gradio_client import Client, handle_file

    ref, ref_text = F5_VOICES[voice]
    try:
        got = Client("mrfakename/E2-F5-TTS", verbose=False, token=env_key("HF_TOKEN") or None).predict(
            handle_file(str(ref)), ref_text, text, True, api_name="/predict")
    except Exception as exc:
        raise VoiceEngineDown(f"f5: {exc}") from exc
    got = got[0] if isinstance(got, (list, tuple)) else got
    shutil.copy(got.get("path", got) if isinstance(got, dict) else got, raw)


EDGE_TRIES = 4


def edge_tts_voice(text: str, cfg: dict, raw: Path) -> None:
    """Free Microsoft edge-tts. GuyNeural +30% / +10Hz = the anime recap narrator (0.86 vs refs 18, 21, 22)."""
    import asyncio

    import edge_tts

    import time

    for attempt in range(EDGE_TRIES):  # the free service drops a line now and then ("No audio was received")
        comm = edge_tts.Communicate(text, cfg.get("edge_voice", "en-US-GuyNeural"),
                                    rate=cfg.get("edge_rate", "+30%"), pitch=cfg.get("edge_pitch", "+10Hz"))
        try:
            asyncio.run(comm.save(str(raw.with_suffix(".mp3"))))
            break
        except Exception as exc:
            if attempt + 1 == EDGE_TRIES:
                raise VoiceEngineDown(f"edge: {exc}") from exc
            print(f"edge try {attempt + 1} failed ({exc}), trying again", flush=True)
            time.sleep(30 * 2 ** attempt)  # a block from the service lifts after a minute or two
    run([FFMPEG, "-y", "-i", str(raw.with_suffix(".mp3")), str(raw)])


def eleven_tts(text: str, voice: str, raw: Path) -> None:
    import urllib.request

    key = env_key("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceEngineDown("elevenlabs: no ELEVENLABS_API_KEY")
    body = json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                       "voice_settings": {"stability": 0.4, "similarity_boost": 0.8, "style": 0.3}}).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICES.get(voice, voice)}?output_format=mp3_44100_128",
        data=body, headers={"xi-api-key": key, "Content-Type": "application/json"})
    try:
        raw.with_suffix(".mp3").write_bytes(urllib.request.urlopen(req, timeout=120).read())
    except Exception as exc:
        raise VoiceEngineDown(f"elevenlabs: {exc}") from exc
    run([FFMPEG, "-y", "-i", str(raw.with_suffix(".mp3")), str(raw)])


def orpheus_tts(text: str, voice: str, raw: Path) -> None:
    from groq import Groq

    try:
        Groq(api_key=groq_key()).audio.speech.create(
            model="canopylabs/orpheus-v1-english", voice=voice, input=text, response_format="wav",
        ).write_to_file(raw)
    except Exception as exc:
        raise VoiceEngineDown(f"orpheus: {exc}") from exc


def kokoro_tts(text: str, voice: str, raw: Path) -> None:
    try:
        import soundfile as sf
        from kokoro_onnx import Kokoro
    except ImportError as exc:  # not installed on the build machine
        raise VoiceEngineDown(f"kokoro: {exc}") from exc

    global KOKORO
    if KOKORO is None:  # load once per build; fetch the model files the first time (the runner cache keeps them)
        KOKORO_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
            if not (KOKORO_DIR / name).exists():
                urllib.request.urlretrieve(KOKORO_URL + name, KOKORO_DIR / name)
        KOKORO = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    samples, rate = KOKORO.create(text, voice=voice, speed=1.0, lang="en-gb" if voice.startswith("b") else "en-us")
    sf.write(raw, samples, rate)


SUPERTONIC = None  # the loaded model, shared by every line
SUPERTONIC_VOICES = ("M1", "M3", "M5")  # the male voices in use; "supertonic_voice" in script.json picks one


def supertonic_tts(text: str, voice: str, raw: Path) -> None:
    try:
        from supertonic import TTS
    except ImportError as exc:  # not installed on the build machine
        raise VoiceEngineDown(f"supertonic: {exc}") from exc

    global SUPERTONIC
    try:
        if SUPERTONIC is None:  # load once per build; the first run downloads the model (the runner cache keeps it)
            SUPERTONIC = TTS(auto_download=True)
        wav, _ = SUPERTONIC.synthesize(text=text, voice_style=SUPERTONIC.get_voice_style(voice_name=voice),
                                       total_steps=8, speed=1.0, lang="en")
        SUPERTONIC.save_audio(wav, str(raw))
    except Exception as exc:
        raise VoiceEngineDown(f"supertonic: {exc}") from exc


def supertonic_voice(cfg: dict) -> str:
    voice = cfg.get("supertonic_voice", "M1")
    if voice not in SUPERTONIC_VOICES:
        print(f"  supertonic_voice '{voice}' is not one of {SUPERTONIC_VOICES}, using M1", flush=True)
        return "M1"
    return voice


ENGINES = {"edge": edge_tts_voice, "f5": f5_tts, "elevenlabs": eleven_tts, "orpheus": orpheus_tts, "chatterbox": chatterbox_tts,
           "kokoro": kokoro_tts, "supertonic": supertonic_tts}


def make_voice(text: str, cfg: dict, proj: Path, out: Path) -> None:
    """TTS on the configured engine, then speed it up with the pitch kept."""
    if out.exists():
        return
    raw = out.with_suffix(".raw.wav")
    if cfg["voice_engine"] == "edge":
        edge_tts_voice(plain(text), cfg, raw)
    elif cfg["voice_engine"] == "f5":
        f5_tts(plain(text), cfg.get("f5_voice", "liam"), raw)
    elif cfg["voice_engine"] == "elevenlabs":
        eleven_tts(plain(text), cfg.get("eleven_voice", "liam"), raw)
    elif cfg["voice_engine"] == "orpheus":
        orpheus_tts(plain(text), cfg.get("orpheus_voice", "troy"), raw)
    elif cfg["voice_engine"] == "chatterbox":
        chatterbox_tts(plain(text), (proj / cfg["voice_ref"]).resolve(), raw)
    elif cfg["voice_engine"] == "supertonic":
        supertonic_tts(plain(text), supertonic_voice(cfg), raw)
    else:
        kokoro_tts(plain(text), cfg.get("kokoro_voice", "am_michael"), raw)
    run([FFMPEG, "-y", "-i", str(raw), "-af",
         f"atempo={cfg['speed']},silenceremove=start_periods=1:start_threshold=-45dB",
         "-ar", "44100", "-ac", "2", str(out)])


WHISPER = None  # the local faster-whisper model, loaded once per build
TIMING_USED = {"faster-whisper": 0, "groq": 0, "estimate": 0}


def norm_word(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def align_words(spoken: list[str], heard: list[tuple[float, float, str]], dur: float = 0.0) -> list[tuple[float, float]] | None:
    """Give every script word a start/end from Whisper's words. Words are matched by text (difflib), so one dropped or
    split word cannot shift every later caption; unmatched words are spread between their matched neighbours."""
    heard = [h for h in heard if norm_word(h[2])]
    want = [norm_word(w) for w in spoken]
    sm = difflib.SequenceMatcher(None, want, [norm_word(h[2]) for h in heard], autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(want)
    matched = 0
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = (heard[b + k][0], heard[b + k][1])
            matched += 1
    if matched < max(1, len(want) * 0.6):  # Whisper heard something else; not worth trusting
        return None
    i = 0
    while i < len(times):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(times) and times[j] is None:
            j += 1
        start = times[i - 1][1] if i else 0.0
        end = times[j][0] if j < len(times) else max(dur, times[i - 1][1] if i else 0.0)  # trailing words Whisper dropped run to the line's end
        end = max(end, start)
        for k in range(i, j):
            times[k] = (start + (end - start) * (k - i) / (j - i), start + (end - start) * (k - i + 1) / (j - i))
        i = j
    return times


def wav_seconds(wav: Path) -> float:
    try:
        import soundfile

        return float(soundfile.info(str(wav)).duration)
    except Exception:
        return 0.0


def local_words(wav: Path, words: list[str]) -> list[tuple[float, float]] | None:
    """Word start/end from local faster-whisper (small.en, CPU); None when it fails or hears something else."""
    global WHISPER
    try:
        if WHISPER is None:
            from faster_whisper import WhisperModel

            WHISPER = WhisperModel("small.en", device="cpu", compute_type="int8")
        segs, _ = WHISPER.transcribe(str(wav), word_timestamps=True, beam_size=1, condition_on_previous_text=False)
        return align_words(words, [(w.start, w.end, w.word) for seg in segs for w in seg.words], wav_seconds(wav))
    except Exception as exc:  # timing is cosmetic; never fail the build on it
        print("  local word timing failed:", type(exc).__name__, str(exc)[:100])
        return None


def groq_words(wav: Path, words: list[str]) -> list[tuple[float, float]] | None:
    try:
        from groq import Groq

        with open(wav, "rb") as fh:
            res = Groq(api_key=groq_key()).audio.transcriptions.create(
                file=(wav.name, fh.read()), model="whisper-large-v3", response_format="verbose_json",
                timestamp_granularities=["word"], language="en")
        return align_words(words, [(w["start"], w["end"], w["word"]) for w in (res.model_dump().get("words") or [])], wav_seconds(wav))
    except Exception as exc:  # timing is cosmetic; never fail the build on it
        print("  word timing fallback:", type(exc).__name__)
        return None


def word_times(text: str, wav: Path, dur: float) -> list[tuple[float, float, str]]:
    """Word timings from local faster-whisper, then Groq Whisper; last resort spreads words by length."""
    words = plain(text).split()
    marked = text.split()
    for name, find in (("faster-whisper", local_words), ("groq", groq_words)):
        got = find(wav, words)
        if got:
            TIMING_USED[name] += 1
            return [(s, e, m) for (s, e), m in zip(got, marked)]
    TIMING_USED["estimate"] += 1
    weights = [len(w) + 2 for w in words]
    total, t, out = sum(weights), 0.0, []
    for wt, m in zip(weights, marked):
        span = dur * wt / total
        out.append((t, t + span, m))
        t += span
    return out


# ---------- picture ----------

def load_shots(proj: Path) -> list[tuple[float, float]]:
    path = proj / "work" / "shots.txt"
    if not path.exists():
        return []
    return [tuple(map(float, line.split())) for line in path.read_text().splitlines() if line.strip()]


def snap(start: float, dur: float, shots: list[tuple[float, float]]) -> float:
    """Move a clip so it stays inside the camera shot that holds `start` (no jump across a cut)."""
    for a, b in shots:
        if a <= start < b:
            if b - a <= dur:
                return a
            return min(start, b - dur - 0.04)
    return start


PANEL_EDGE = 0.12  # three frames kept clear of each panel edge: seeking can land one frame early or late


def inside_panel(start: float, shots: list[tuple[float, float]]) -> float:
    """Comic start moved clear of panel edges, so no frame of the panel before or after flashes on screen."""
    for k, (a, b) in enumerate(shots):
        if a <= start < b:
            if b - start <= 0.5 and k + 1 < len(shots):  # a blink before a cut: the next panel is the real one
                return shots[k + 1][0] + PANEL_EDGE
            return min(max(start, a + PANEL_EDGE), b - 2 * PANEL_EDGE)
    return start


def shot_room(start: float, shots: list[tuple[float, float]]) -> float:
    """Seconds left in the panel that holds `start` (a clip longer than this holds its last frame)."""
    for a, b in shots:
        if a <= start < b:
            return max(b - start - PANEL_EDGE, 0.04)
    return 1e9


def dt(text: str) -> str:
    """Escape text for ffmpeg drawtext inside a filter graph (colons, quotes, backslashes)."""
    return text.replace("\\", "\\\\").replace(":", "\:").replace("'", "’")


def music_drop(path: Path, need: float) -> float:
    """First second where the track reaches full strength (skips the quiet intro), like the reference reels."""
    import numpy as np

    pcm = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    step = 4000  # 0.5 s
    rms = np.sqrt(np.array([np.mean(x[i:i + step] ** 2) for i in range(0, len(x) - step, step)]) + 1e-9)
    smooth = np.convolve(rms, np.ones(8) / 8, mode="same")  # 4 s window
    full = 0.85 * np.percentile(smooth, 90)
    latest = max(0.0, len(rms) * 0.5 - need - 2)  # keep enough track left for the whole short
    hit = next((i * 0.5 for i, v in enumerate(smooth) if v >= full), 0.0)
    return hit if hit > latest else min(hit, latest)  # long videos loop the track anyway


def mean_db(path: Path) -> float:
    out = subprocess.run([FFMPEG, "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", out).group(1))


SFX_DIR = Path(__file__).resolve().parent.parent / "sfx"
SFX_DB_UNDER_VOICE = 8.0  # an effect sits this far under the voice, so it never covers a word


def sfx_file(i: int, seg: dict) -> Path | None:
    """The sound file for a segment's "sfx" key; an unknown name is ignored with a log line."""
    name = seg.get("sfx")
    if not name:
        return None
    found = next(iter(sorted(SFX_DIR.glob(f"{name}.*"))), None) if isinstance(name, str) and re.fullmatch(r"\w+", name) else None
    if not found:
        print(f"  sfx segment {i:02d}: unknown effect {name!r}, ignored", flush=True)
    return found


def piece(src: Path, start: float, dur: float, out: Path, audio: Path | None = None, full: bool = False,
          room: float = 1e9) -> None:
    """One source shot, framed 9:16: sharp centre crop over a blurred, darkened copy.
    `audio` swaps the film sound for another file on the same clock (the no-voice stem)."""
    cut = f"crop=iw:ih*{1 - CROP_BOTTOM:.3f}:0:0," if CROP_BOTTOM else ""
    if full and WIDE:  # tall comic panels in a 16:9 frame: whole panel in the middle, blurred copy behind
        vf = (f"[0:v]{cut}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=30:2,eq=brightness=-0.15[bg];[b]scale=-2:{H}[fg];"
              f"[bg][fg]overlay=(W-w)/2:0,setsar=1,fps={FPS}[v]")
    elif full:  # manhwa panels already fit the frame: fill the screen
        vf = f"[0:v]{cut}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}[v]"
    elif WIDE:  # a film in 16:9: fill the screen, pushed in a little
        vf = (f"[0:v]{cut}scale={round(W * FILM_ZOOM) // 2 * 2}:{round(H * FILM_ZOOM) // 2 * 2}:"
              f"force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}[v]")
    else:
        vf = (f"[0:v]{cut}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur=30:2,eq=brightness=-0.12[bg];[b]scale=-2:{round(FG_HEIGHT * FILM_ZOOM) // 2 * 2},crop={W}:{FG_HEIGHT}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[v]")
    take = min(dur, room)  # never read past the shot; hold its last frame for the rest
    if take < dur:
        vf = vf[:-3] + f",tpad=stop_mode=clone:stop_duration={dur - take:.3f}[v]"
    extra = ["-ss", f"{start:.3f}", "-t", f"{take:.3f}", "-i", str(audio)] if audio else []
    run([FFMPEG, "-y", "-ss", f"{start:.3f}", "-t", f"{take:.3f}", "-i", str(src), *extra, "-filter_complex", vf,
         "-map", "[v]", "-map", "1:a" if audio else "0:a", "-af", "apad", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])


DEPTH_MAX = 12  # depth segments per video; the render is slow (about a minute per 4 s on the runner)
DEPTH_SCENE = Path(__file__).resolve().parent / "depth_scene.py"
DEPTH_PY = os.environ.get("DEPTHFLOW_PY", "")  # python of the environment that has depthflow + torch; cleared when a render hangs


def depth_frame(src: Path, at: float, out: Path) -> None:
    """The panel at second `at`, letterbox cut off, laid out for the frame as piece() does."""
    from PIL import Image, ImageFilter

    run([FFMPEG, "-y", "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1", str(out)])
    art = Image.open(out).convert("RGB")
    box = art.convert("L").point(lambda v: 255 if v > 22 else 0).getbbox()  # the source letterboxes the panel in black
    if box and (box[2] - box[0]) > art.width // 3 and (box[3] - box[1]) > art.height // 5:
        art = art.crop(box)
    cover = max(W / art.width, H / art.height)
    back = art.resize((round(art.width * cover), round(art.height * cover)))
    left, top = (back.width - W) // 2, (back.height - H) // 2
    back = back.crop((left, top, left + W, top + H))
    if WIDE:  # whole panel in the middle, blurred copy behind
        back = back.filter(ImageFilter.GaussianBlur(30)).point(lambda v: int(v * 0.85))
        fit = H / art.height
        back.paste(art.resize((round(art.width * fit), H)), ((W - round(art.width * fit)) // 2, 0))
    back.save(out)


def depth_render(cmd: list[str], timeout: float) -> None:
    """Run one DepthFlow render in its own process group, so a hung render (xvfb + GL children) is killed whole."""
    global DEPTH_PY
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:  # it ended on its own just now
            pass
        p.communicate()
        DEPTH_PY = ""  # one hang means the rest of this video renders normal clips
        raise RuntimeError(f"render hung past {timeout:.0f}s; depth is off for the rest of this video")
    if p.returncode:
        raise RuntimeError(f"exit {p.returncode}: {output[-300:]}")


def depth_segment(i: int, src: Path, at: float, dur: float, tmp: Path) -> Path | None:
    """The panel as a DepthFlow clip as long as the line, with a silent track; None when it fails (normal clip then)."""
    out = tmp / f"s{i:02d}_depth_{at:.2f}_{dur:.2f}.mp4"
    if out.exists():
        return out
    try:
        if not DEPTH_PY:
            raise RuntimeError("DEPTHFLOW_PY is not set")
        frame, raw = tmp / f"s{i:02d}_depth.png", tmp / f"s{i:02d}_depth_raw.mp4"
        depth_frame(src, at, frame)
        depth_render(["xvfb-run", "-a", DEPTH_PY, str(DEPTH_SCENE), str(frame), str(raw), f"{dur:.3f}", str(FPS), str(W), str(H)],
                     120 + dur * 30)
        if not raw.exists():
            raise RuntimeError("no render output")
        # a render shorter than the line holds its last frame, so the voice is never cut
        run([FFMPEG, "-y", "-i", str(raw), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
             "-vf", f"tpad=stop_mode=clone:stop_duration={dur:.3f}", "-t", f"{dur:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])
        return out
    except Exception as exc:  # a key panel is a bonus; never fail the build on it
        print(f"  warning: depth segment {i:02d} failed, using the normal clip: {str(exc)[:300]}", flush=True)
        return None


def video_length(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return float(out) if out and out != "N/A" else duration(path)


def concat(parts: list[Path], out: Path) -> None:
    """Join clips. Pictures are copied; sound is joined as raw samples, each part cut to its picture's
    length. Copying the AAC sound instead adds a ~23 ms gap at every join, so the voice stutters and drifts."""
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    sound = []
    for p in parts:
        wav = p.with_suffix(".join.wav")
        run([FFMPEG, "-y", "-i", str(p), "-vn", "-af", "apad", "-t", f"{video_length(p):.6f}",
             "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", str(wav)])
        sound.append(wav)
    sounds = out.with_suffix(".wav.txt")
    sounds.write_text("".join(f"file '{w.as_posix()}'\n" for w in sound), encoding="utf-8")
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-f", "concat", "-safe", "0", "-i", str(sounds),
         "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", str(out)])
    for w in sound:
        w.unlink()


def narration_segment(i: int, seg: dict, cfg: dict, proj: Path, tmp: Path) -> tuple[Path, float, list]:
    # one folder per engine + voice: a failed engine never wipes lines another engine made
    vdir = tmp / f"voice-{cfg['voice_engine']}-{supertonic_voice(cfg) if cfg['voice_engine'] == 'supertonic' else cfg.get(cfg['voice_engine'] + '_voice', 'default')}"
    vdir.mkdir(exist_ok=True)
    voice = vdir / f"v{i:02d}.wav"
    make_voice(seg["say"], cfg, proj, voice)
    dur = duration(voice) + 0.12
    words = word_times(seg["say"], voice, dur - 0.12)
    clips = [c if isinstance(c, list) else [c, None] for c in seg["clips"]]
    # each shot starts on its word (given, or an even share of the words), so picture follows the line
    # a word number past the end of the line (a typo, or the transcript merged words) means the last word
    cuts = [0.0] + [words[min(w, len(words) - 1)][0] if w is not None else words[round(j * len(words) / len(clips))][0]
                    for j, (_, w) in enumerate(clips) if j]
    cuts.append(dur)
    for k in range(1, len(cuts) - 1):  # every shot stays on screen at least half a second
        cuts[k] = min(max(cuts[k], cuts[k - 1] + 0.5), dur - 0.5 * (len(cuts) - 1 - k))
    bed = proj / cfg["bed_audio"] if cfg.get("bed_audio") else None
    shots = load_shots(proj)
    parts = []
    depth = None
    if i in cfg.get("_depth_ok", ()):  # key panel: its first clip, as a DepthFlow clip, covers the whole line
        at = inside_panel(snap(clips[0][0], dur, shots), shots)
        depth = depth_segment(i, proj / cfg["source"], at, dur, tmp)
        if depth:
            print(f"  depth segment {i:02d}: DepthFlow used", flush=True)
    for j, (start, _) in enumerate(clips if not depth else []):
        span = cuts[j + 1] - cuts[j]
        p = tmp / f"s{i:02d}_{j}.mp4"
        comic = cfg.get("frame") == "full"  # a film clip may run on across a cut (a normal edit); a comic panel never spills
        at = snap(start, span, shots)
        at = inside_panel(at, shots) if comic else at
        piece(proj / cfg["source"], at, span, p, bed, comic, shot_room(at, shots) if comic else 1e9)
        parts.append(p)
    video = depth or tmp / f"s{i:02d}_pic.mp4"
    if not depth:
        concat(parts, video)
    out = tmp / f"seg{i:02d}.mp4"
    fx = sfx_file(i, seg)
    extra, mix = [], "[bed][1:a]amix=inputs=2:duration=first:normalize=0[a]"
    if fx:  # the effect starts with the line, mean level 8 dB under the voice (never above 0 dB gain)
        gain = min(mean_db(voice) - SFX_DB_UNDER_VOICE - mean_db(fx), 0.0)
        extra = ["-i", str(fx)]
        mix = f"[2:a]volume={gain:.1f}dB[fx];[bed][1:a][fx]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[a]"
        print(f"  sfx segment {i:02d}: {fx.name} at {gain:.1f} dB", flush=True)
    run([FFMPEG, "-y", "-i", str(video), "-i", str(voice), *extra, "-filter_complex",
         f"[0:a]volume={cfg['bed_volume']}[bed];{mix}",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])
    return out, video_length(out), words


def original_segment(i: int, seg: dict, cfg: dict, proj: Path, tmp: Path) -> tuple[Path, float, list]:
    start, end = seg["orig"]
    out = tmp / f"seg{i:02d}.mp4"
    piece(proj / cfg["source"], start, end - start, out, full=cfg.get("frame") == "full")
    return out, video_length(out), [(0.0, end - start, "QUOTE:" + seg["text"])]


# ---------- captions ----------

def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def karaoke_events(events: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Per-word events -> one event per word that shows its whole caption line with that word marked.
    A line breaks after punctuation or when it would pass a width that fits the screen; a quote stays as it is."""
    limit = 30 if WIDE else 11  # 9:16: 11 characters at 104 px fit between the margins even with the lit word at 115%
    out, line = [], []

    def flush() -> None:
        for k, (start, end, _) in enumerate(line):
            stop = line[k + 1][0] if k + 1 < len(line) else end
            words = [re.sub(r"[*]", "", w).upper() for _, _, w in line]
            out.append((start, max(stop, start + 0.05), "KARAOKE:" + "\x1f".join(
                (" ".join(words[:k]) + " " if k else "", words[k], " " + " ".join(words[k + 1:]) if k + 1 < len(words) else ""))))
        line.clear()

    for ev in events:
        if ev[2].startswith("QUOTE:"):
            flush()
            out.append(ev)
            continue
        if line and sum(len(re.sub(r"[*]", "", w)) + 1 for _, _, w in line) + len(ev[2]) > limit:
            flush()
        line.append(ev)
        if re.search(r"[.,!?;:]\W*$", ev[2]):
            flush()
    flush()
    return out


def captions(events: list[tuple[float, float, str]], path: Path, karaoke: bool = False) -> None:
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            f"Style: Word,Arial Black,{76 if WIDE else 104},&H00FFFFFF,&H00000000,&H64000000,1,0,1,7,3,5,40,40,0\n"
            f"Style: Quote,Arial,{46 if WIDE else 60},&H00FFFFFF,&H00000000,&H64000000,1,1,1,4,2,2,80,80,"
            f"{70 if WIDE else 330}\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    if karaoke:
        events = karaoke_events(events)
    for start, end, word in events:
        if word.startswith("KARAOKE:"):  # whole line, the spoken word (middle of the two | marks) yellow and a bit bigger
            before, now, after = word[8:].split("\x1f")  # a unit separator, never in a word (a "|" could be)
            lit = f"{{\\c{YELLOW}\\fscx115\\fscy115}}"
            text = f"{before}{lit}{now}{{\\r}}{after}"
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Word,,0,0,0,,"
                         f"{{\\pos({W // 2},{int(H * (0.82 if WIDE else 0.755))})}}{text}")
            continue
        if word.startswith("QUOTE:"):
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Quote,,0,0,0,,“{word[6:]}”")
            continue
        key = word.startswith("*")
        text = re.sub(r"[*]", "", word).upper()
        colour = f"\\c{YELLOW}" if key else ""
        pop = "\\fscx118\\fscy118\\t(0,90,\\fscx100\\fscy100)"
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Word,,0,0,0,,{{\\pos({W // 2},{int(H * (0.82 if WIDE else 0.755))}){pop}{colour}}}{text}")
    path.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- main ----------

def build_segments(cfg: dict, proj: Path, tmp: Path) -> tuple[list[Path], list]:
    segs, events, clock = [], [], 0.0
    cfg["_starts"] = []  # where each segment begins in the joined video
    for i, seg in enumerate(cfg["segments"]):
        maker = original_segment if "orig" in seg else narration_segment
        path, dur, words = maker(i, seg, cfg, proj, tmp)
        segs.append(path)
        cfg["_starts"].append(clock)
        events += [(clock + s, clock + min(e, dur), w) for s, e, w in words]
        clock += dur
        print(f"segment {i:02d} ok ({dur:.1f}s)", flush=True)
    return segs, events


def stamp(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def write_chapters(cfg: dict, total: float, path: Path) -> None:
    """YouTube chapters from the segments marked "chapter": "M:SS Title" lines; none unless YouTube would accept them
    (first at 0:00, at least 3, each at least 10 s long)."""
    marks = [(start, str(seg["chapter"]).strip()) for start, seg in zip(cfg["_starts"], cfg["segments"]) if seg.get("chapter")]
    if not marks:
        return
    if marks[0][0] > 0.5:
        marks.insert(0, (0.0, "Intro"))
    marks[0] = (0.0, marks[0][1])
    ends = [m[0] for m in marks[1:]] + [total]
    if len(marks) < 3 or any(e - m[0] < 10 for m, e in zip(marks, ends)):
        print(f"chapters: {len(marks)} marked, not valid for YouTube (need 3+, each 10 s+), file not written", flush=True)
        path.unlink(missing_ok=True)
        return
    path.write_text("\n".join(f"{stamp(t)} {title}" for t, title in marks) + "\n", encoding="utf-8")
    print(f"chapters: {len(marks)} written to {path.name}", flush=True)


def main() -> None:
    proj = Path(sys.argv[1]).resolve()
    script = proj / (sys.argv[2] if len(sys.argv) > 2 else "script.json")
    cfg = json.loads(script.read_text(encoding="utf-8"))
    global W, H, WIDE, CROP_BOTTOM
    CROP_BOTTOM = cfg.get("crop_bottom", 0.0)
    if cfg.get("aspect") == "16:9":  # YouTube long version
        W, H, WIDE = 1920, 1080, True
    # each script gets its own build folder, so cached voice lines never mix between versions
    tmp = proj / ("build" if script.name == "script.json" else f"build-{script.stem}")
    tmp.mkdir(exist_ok=True)
    if cfg.get("frame") == "full":  # manhwa only: segments marked "depth": true, the first DEPTH_MAX of them
        marked = [i for i, seg in enumerate(cfg["segments"]) if seg.get("depth") and "say" in seg]
        cfg["_depth_ok"] = set(marked[:DEPTH_MAX])
        for i in marked[DEPTH_MAX:]:
            print(f"depth segment {i:02d} skipped: over the cap of {DEPTH_MAX}", flush=True)
    chain = [e for e in cfg.get("voice_chain", ["f5", "elevenlabs", "orpheus", "kokoro"]) if e in ENGINES]
    for engine in chain:
        cfg["voice_engine"] = engine
        try:
            segs, events = build_segments(cfg, proj, tmp)
            break
        except VoiceEngineDown as exc:
            print(f"voice engine '{engine}' down, trying next: {str(exc)[:120]}", flush=True)
    else:
        sys.exit("every voice engine failed")
    print("voice engine used:", cfg["voice_engine"])
    print("word timing used:", TIMING_USED)
    joined = tmp / "joined.mp4"
    concat(segs, joined)
    captions(events, tmp / "captions.ass", bool(cfg.get("karaoke")))
    mark_y, credit_y = ("40", "h-50") if WIDE else ("210", "h-150")
    header = cfg.get("header") and not WIDE  # page name card in the empty band above the picture
    if header:
        card = proj / cfg["header"]
        # comic panels fill the whole frame at full size, so they get only the small round logo, not the card
        shutil.copy(card.with_name("logo.png") if cfg.get("frame") == "full" else card, tmp / "header.png")
    mark = "" if header else (  # the card already carries the @handle
        f"drawtext=fontfile='{FONT}':text='{dt(cfg['watermark'])}':x=(w-tw)/2:y={mark_y}:fontsize=44:"
        "fontcolor=white@0.85:box=1:boxcolor=black@0.35:boxborderw=12,")
    vf = ("ass=captions.ass," + mark +
          f"drawtext=fontfile='{FONT}':text='{dt(cfg['credit'])}':x=(w-tw)/2:y={credit_y}:fontsize=26:fontcolor=white@0.75")
    if header:
        if cfg.get("frame") == "full":  # small logo in the top left corner, under the app's back button
            vf = (f"movie=header.png,scale={LOGO_SIZE}:-1,format=rgba,colorchannelmixer=aa=0.9[hd];"
                  f"[in]{vf}[b];[b][hd]overlay={LOGO_X}:{LOGO_Y}[out]")
        else:  # a film leaves an empty band above the picture for the full card
            vf = f"movie=header.png[hd];[in]{vf}[b];[b][hd]overlay=(W-w)/2:{HEADER_Y}[out]"
    final = proj / cfg.get("output", "final_short.mp4")
    if cfg.get("music"):
        # steady music bed set N dB under the voice track, as measured on the reference videos (13.5 dB)
        music = (proj / cfg["music"]).resolve()
        trim = next((db for name, db in MUSIC_TRIM.items() if name in music.name), 0.0)
        gain = mean_db(joined) - cfg.get("music_db_under_voice", 13.5) - trim - mean_db(music)
        start = cfg.get("music_start", "auto")
        start = music_drop(music, duration(joined)) if start == "auto" else float(start)
        print(f"music starts at {start:.1f}s of the track")
        # the music goes silent for the last line, so the cliffhanger lands in quiet
        last = duration(joined) - video_length(segs[-1])
        out = (f",afade=t=out:st={max(last - MUSIC_OUT, 0):.2f}:d={MUSIC_OUT}"
               if cfg.get("music_off_last", True) and len(segs) > 1 else "")
        if cfg.get("music_end") == "swell":  # music rises under the last line instead of going quiet
            out = f",volume=2dB:enable='gte(t,{last:.2f})'"
        delay = float(cfg.get("music_delay", 0))  # voice alone for the hook, music comes in after N seconds
        lead = f"adelay={delay * 1000:.0f}:all=1," if delay else ""
        audio = ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(music), "-filter_complex",
                 f"[1:a]{lead}volume={gain:.1f}dB,afade=t=in:st={delay:.2f}:d=0.05{out}[m];"
                 "[0:a][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5[a]",
                 "-map", "0:v", "-map", "[a]"]
    else:
        audio = ["-af", "loudnorm=I=-14:TP=-1.5"]
    run([FFMPEG, "-y", "-i", "joined.mp4", *audio, "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(final)], cwd=tmp)
    write_chapters(cfg, duration(final), final.with_suffix(".chapters.txt"))
    print(f"done {final} ({duration(final):.1f}s)")


if __name__ == "__main__":
    main()
