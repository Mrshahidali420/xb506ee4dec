"""TIMING test: word timestamps of the one-line Kokoro mp3 (python timing.py whisperx|faster-whisper MP3)."""
import sys

from common import pkg, run_tool

MP3 = sys.argv[2]


def first20(words):
    return " / ".join(f"{w[0]}@{w[1]:.2f}-{w[2]:.2f}" for w in words[:20])


def fw(d):
    from faster_whisper import WhisperModel
    m = WhisperModel("small.en", device="cpu", compute_type="int8")
    segs, _ = m.transcribe(MP3, word_timestamps=True)
    words = [(w.word.strip(), w.start, w.end) for s in segs for w in s.words]
    (d / "faster-whisper_words.txt").write_text("\n".join(f"{a:.2f}\t{b:.2f}\t{w}" for w, a, b in words))
    return ["faster-whisper_words.txt"], "first 20 words: " + first20(words)


def wx(d):
    import whisperx
    m = whisperx.load_model("small.en", "cpu", compute_type="int8", language="en")
    audio = whisperx.load_audio(MP3)
    res = m.transcribe(audio, batch_size=4)
    al, meta = whisperx.load_align_model(language_code="en", device="cpu")
    res = whisperx.align(res["segments"], al, meta, audio, "cpu")
    words = [(w["word"], w.get("start", -1), w.get("end", -1)) for s in res["segments"] for w in s["words"]]
    (d / "whisperx_words.txt").write_text("\n".join(f"{a:.2f}\t{b:.2f}\t{w}" for w, a, b in words))
    return ["whisperx_words.txt"], "first 20 words: " + first20(words)


if __name__ == "__main__":
    if sys.argv[1] == "whisperx":
        run_tool("timing", "WhisperX", wx, lambda: pkg("whisperx", "faster-whisper", "ctranslate2"))
    else:
        run_tool("timing", "faster-whisper", fw, lambda: pkg("faster-whisper", "ctranslate2"))
