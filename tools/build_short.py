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

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import truststore

truststore.inject_into_ssl()

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
W, H, FPS = 1080, 1920, 30  # 9:16 default; "aspect": "16:9" in script.json switches to 1920x1080
FG_HEIGHT = 900
HEADER_Y = 150  # vertical videos: top of the page name card, in the band above the 900 px picture
WIDE = False
CROP_BOTTOM = 0.0  # share of the source height to cut off (burned-in film subtitles)
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
            time.sleep(5 * (attempt + 1))
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

    tts = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    samples, rate = tts.create(text, voice=voice, speed=1.0, lang="en-us")
    sf.write(raw, samples, rate)


ENGINES = {"edge": edge_tts_voice, "f5": f5_tts, "elevenlabs": eleven_tts, "orpheus": orpheus_tts, "chatterbox": chatterbox_tts, "kokoro": kokoro_tts}


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
    else:
        kokoro_tts(plain(text), cfg.get("kokoro_voice", "am_michael"), raw)
    run([FFMPEG, "-y", "-i", str(raw), "-af",
         f"atempo={cfg['speed']},silenceremove=start_periods=1:start_threshold=-45dB",
         "-ar", "44100", "-ac", "2", str(out)])


def word_times(text: str, wav: Path, dur: float) -> list[tuple[float, float, str]]:
    """Word timings from Groq Whisper; falls back to spreading words by length."""
    words = plain(text).split()
    marked = text.split()
    try:
        from groq import Groq

        with open(wav, "rb") as fh:
            res = Groq(api_key=groq_key()).audio.transcriptions.create(
                file=(wav.name, fh.read()), model="whisper-large-v3", response_format="verbose_json",
                timestamp_granularities=["word"], language="en")
        got = [(w["start"], w["end"]) for w in (res.model_dump().get("words") or [])]
        if len(got) == len(words):
            return [(s, e, m) for (s, e), m in zip(got, marked)]
    except Exception as exc:  # timing is cosmetic; never fail the build on it
        print("  word timing fallback:", type(exc).__name__)
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


def piece(src: Path, start: float, dur: float, out: Path, audio: Path | None = None, full: bool = False,
          room: float = 1e9) -> None:
    """One source shot, framed 9:16: sharp centre crop over a blurred, darkened copy.
    `audio` swaps the film sound for another file on the same clock (the no-voice stem)."""
    cut = f"crop=iw:ih*{1 - CROP_BOTTOM:.3f}:0:0," if CROP_BOTTOM else ""
    if full and WIDE:  # tall comic panels in a 16:9 frame: whole panel in the middle, blurred copy behind
        vf = (f"[0:v]{cut}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=30:2,eq=brightness=-0.15[bg];[b]scale=-2:{H}[fg];"
              f"[bg][fg]overlay=(W-w)/2:0,setsar=1,fps={FPS}[v]")
    elif full or WIDE:  # source already fits the frame (manhwa panels, or a film in 16:9): fill the screen
        vf = f"[0:v]{cut}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}[v]"
    else:
        vf = (f"[0:v]{cut}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"boxblur=30:2,eq=brightness=-0.12[bg];[b]scale=-2:{FG_HEIGHT},crop={W}:{FG_HEIGHT}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[v]")
    take = min(dur, room)  # never read past the shot; hold its last frame for the rest
    if take < dur:
        vf = vf[:-3] + f",tpad=stop_mode=clone:stop_duration={dur - take:.3f}[v]"
    extra = ["-ss", f"{start:.3f}", "-t", f"{take:.3f}", "-i", str(audio)] if audio else []
    run([FFMPEG, "-y", "-ss", f"{start:.3f}", "-t", f"{take:.3f}", "-i", str(src), *extra, "-filter_complex", vf,
         "-map", "[v]", "-map", "1:a" if audio else "0:a", "-af", "apad", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)])


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
    vdir = tmp / f"voice-{cfg['voice_engine']}-{cfg.get(cfg['voice_engine'] + '_voice', 'default')}"
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
    for j, (start, _) in enumerate(clips):
        span = cuts[j + 1] - cuts[j]
        p = tmp / f"s{i:02d}_{j}.mp4"
        comic = cfg.get("frame") == "full"  # a film clip may run on across a cut (a normal edit); a comic panel never spills
        at = snap(start, span, shots)
        at = inside_panel(at, shots) if comic else at
        piece(proj / cfg["source"], at, span, p, bed, comic, shot_room(at, shots) if comic else 1e9)
        parts.append(p)
    video = tmp / f"s{i:02d}_pic.mp4"
    concat(parts, video)
    out = tmp / f"seg{i:02d}.mp4"
    run([FFMPEG, "-y", "-i", str(video), "-i", str(voice), "-filter_complex",
         f"[0:a]volume={cfg['bed_volume']}[bed];[bed][1:a]amix=inputs=2:duration=first:normalize=0[a]",
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


def captions(events: list[tuple[float, float, str]], path: Path) -> None:
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            f"Style: Word,Arial Black,{76 if WIDE else 104},&H00FFFFFF,&H00000000,&H64000000,1,0,1,7,3,5,40,40,0\n"
            f"Style: Quote,Arial,{46 if WIDE else 60},&H00FFFFFF,&H00000000,&H64000000,1,1,1,4,2,2,80,80,"
            f"{70 if WIDE else 330}\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for start, end, word in events:
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
    for i, seg in enumerate(cfg["segments"]):
        maker = original_segment if "orig" in seg else narration_segment
        path, dur, words = maker(i, seg, cfg, proj, tmp)
        segs.append(path)
        events += [(clock + s, clock + min(e, dur), w) for s, e, w in words]
        clock += dur
        print(f"segment {i:02d} ok ({dur:.1f}s)", flush=True)
    return segs, events


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
    joined = tmp / "joined.mp4"
    concat(segs, joined)
    captions(events, tmp / "captions.ass")
    mark_y, credit_y = ("40", "h-50") if WIDE else ("210", "h-150")
    header = cfg.get("header") and not WIDE  # page name card in the empty band above the picture
    if header:
        shutil.copy(proj / cfg["header"], tmp / "header.png")
    mark = "" if header else (  # the card already carries the @handle
        f"drawtext=fontfile='{FONT}':text='{dt(cfg['watermark'])}':x=(w-tw)/2:y={mark_y}:fontsize=44:"
        "fontcolor=white@0.85:box=1:boxcolor=black@0.35:boxborderw=12,")
    vf = ("ass=captions.ass," + mark +
          f"drawtext=fontfile='{FONT}':text='{dt(cfg['credit'])}':x=(w-tw)/2:y={credit_y}:fontsize=26:fontcolor=white@0.75")
    if header:
        vf = f"movie=header.png[hd];[in]{vf}[b];[b][hd]overlay=(W-w)/2:{HEADER_Y}[out]"
    final = proj / cfg.get("output", "final_short.mp4")
    if cfg.get("music"):
        # steady music bed set N dB under the voice track, as measured on the reference videos (13.5 dB)
        music = (proj / cfg["music"]).resolve()
        gain = mean_db(joined) - cfg.get("music_db_under_voice", 13.5) - mean_db(music)
        start = cfg.get("music_start", "auto")
        start = music_drop(music, duration(joined)) if start == "auto" else float(start)
        print(f"music starts at {start:.1f}s of the track")
        audio = ["-stream_loop", "-1", "-ss", f"{start:.2f}", "-i", str(music), "-filter_complex",
                 f"[1:a]volume={gain:.1f}dB,afade=t=in:d=0.05[m];"
                 "[0:a][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5[a]",
                 "-map", "0:v", "-map", "[a]"]
    else:
        audio = ["-af", "loudnorm=I=-14:TP=-1.5"]
    run([FFMPEG, "-y", "-i", "joined.mp4", *audio, "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(final)], cwd=tmp)
    print(f"done {final} ({duration(final):.1f}s)")


if __name__ == "__main__":
    main()
