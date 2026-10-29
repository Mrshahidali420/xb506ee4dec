"""Transcribe an audio file with Groq Whisper and save segments + word timings as JSON.

Usage: python groq_transcribe.py <audio> <out.json> [language]
The key is read from GROQ_API_KEY or ~/.config/watch/.env.
"""
import json
import os
import sys
from pathlib import Path

import truststore

truststore.inject_into_ssl()

from groq import Groq  # noqa: E402


def load_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    env = Path.home() / ".config" / "watch" / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GROQ_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("GROQ_API_KEY not found")
    return key


def main() -> None:
    audio, out = sys.argv[1], sys.argv[2]
    language = sys.argv[3] if len(sys.argv) > 3 else None
    client = Groq(api_key=load_key())
    with open(audio, "rb") as fh:
        result = client.audio.transcriptions.create(
            file=(os.path.basename(audio), fh.read()),
            model="whisper-large-v3",
            response_format="verbose_json",
            timestamp_granularities=["word", "segment"],
            language=language,
        )
    data = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    keep = {
        "text": data.get("text", ""),
        "segments": [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in data.get("segments") or []],
        "words": [{"start": w["start"], "end": w["end"], "word": w["word"].strip()} for w in data.get("words") or []],
    }
    Path(out).write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ok {len(keep['segments'])} segments, {len(keep['words'])} words")


if __name__ == "__main__":
    main()
