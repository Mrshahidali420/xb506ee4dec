"""Per-second signals from an episode. Everything runs on CPU with ffmpeg, librosa, scenedetect and pysubs2."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

SR = 22050
HOP = 512
MOTION_FPS = 3
MOTION_SIZE = (160, 90)


def run(args: list[str]) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def extract_wav(video: Path, wav: Path) -> None:
    run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", str(SR), str(wav)])


def per_second(frames: np.ndarray, n: int, how=np.mean) -> np.ndarray:
    """Collapse a frame-rate series (HOP samples per frame) to one value per second."""
    per = SR / HOP
    out = np.zeros(n)
    for t in range(n):
        seg = frames[int(t * per):int((t + 1) * per)]
        out[t] = how(seg) if len(seg) else 0.0
    return out


def audio_signals(wav: Path, n: int) -> dict[str, np.ndarray]:
    """loud, swell, speech, bass: all in the sound alone."""
    import librosa

    y, _ = librosa.load(str(wav), sr=SR, mono=True)
    stft = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    total = stft.sum(axis=0) + 1e-9
    rms_db = librosa.amplitude_to_db(librosa.feature.rms(S=np.sqrt(stft), frame_length=2048, hop_length=HOP)[0] + 1e-6)

    loud = per_second(rms_db, n)
    onset = per_second(librosa.onset.onset_strength(S=librosa.power_to_db(stft), sr=SR, hop_length=HOP), n)
    rise = np.maximum(loud - np.array([loud[max(0, t - 8):t].mean() if t else loud[0] for t in range(n)]), 0)
    speech_band = (freqs >= 300) & (freqs <= 3400)
    bass_band = (freqs >= 20) & (freqs <= 150)
    speech = per_second(10 * np.log10(stft[speech_band].sum(axis=0) / total + 1e-9) + rms_db, n)
    bass = per_second(10 * np.log10(stft[bass_band].sum(axis=0) + 1e-9), n)
    return {"loud": loud, "swell": 0.5 * onset / (onset.max() + 1e-9) + 0.5 * rise / (rise.max() + 1e-9), "speech": speech, "bass": bass}


def scene_cuts(video: Path) -> list[float]:
    from scenedetect import ContentDetector, detect

    scenes = detect(str(video), ContentDetector(threshold=27.0), show_progress=False)
    return [s[0].get_seconds() for s in scenes[1:]]


def motion(video: Path, n: int) -> np.ndarray:
    """Mean frame difference, sampled at 3 fps. The jump at a scene cut is capped so a cut is not 'motion'."""
    w, h = MOTION_SIZE
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={MOTION_FPS},scale={w}:{h},format=gray",
                           "-f", "rawvideo", "-"], capture_output=True, check=True)
    frames = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(-1, h, w).astype(np.int16)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    diff = np.minimum(diff, np.percentile(diff, 95))
    out = np.zeros(n)
    for t in range(n):
        seg = diff[int(t * MOTION_FPS):int((t + 1) * MOTION_FPS)]
        out[t] = seg.mean() if len(seg) else 0.0
    return out


def subtitle_density(subs: Path, n: int) -> np.ndarray:
    """Characters shown per second, plus a nudge for '!' and '?'."""
    import pysubs2

    out = np.zeros(n)
    for line in pysubs2.load(str(subs)):
        text = line.plaintext
        weight = len(text) + 4 * (text.count("!") + text.count("?"))
        a, b = int(line.start / 1000), min(n, int(line.end / 1000) + 1)
        if b > a:
            out[a:b] += weight / (b - a)
    return out
