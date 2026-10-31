"""Write a job's script.json with a free LLM (Groq), from the OCR text of its panels.

Usage: python write_script.py jobs/auto-<slug>-p<N>
Needs <job>/meta.json (from pick.py), <job>/work/panels_text.json (from manhwa_panels.py) and GROQ_API_KEY
(environment, or ~/.config/watch/.env). The key is never printed.
The LLM writes the story lines, clip picks, title, caption hook, comment and mood. Everything else
(voice, frame, header, watermark) is copied from the hand-made example script so the look stays the same.
The answer is checked (clip seconds, word indexes, length); a failed check is sent back, up to 3 tries.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "jobs" / "manhwa-swordmaster-son-p1" / "script.json"
URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
TRIES = 3
STEP = 3                      # seconds per panel, same as manhwa_panels.SECONDS
TEXT_BUDGET = 10000           # characters of panel text; the free tier allows 8000 tokens per request, answer included
ANSWER_TOKENS = 3800          # room for the script (about 1300 words + clips) and a short reasoning pass
SEGMENTS = (45, 70)
WORDS = (450, 1300)           # about 3 to 8 minutes at the narrator's pace
LINE_WORDS = (4, 25)
MUSIC_DEFAULT, MUSIC_SUSPENSE = "../../music/Kamin_EMIN_JONY.mp3", "../../music/Lordly_Feder.mp3"
HASHTAGS = "#manhwa #manhwarecap #manga #recap #fyp"
FIXED_DROP = {"title", "caption", "comment", "segments"}

RULES = f"""You write the narration for a vertical manhwa recap video. Output ONE JSON object and nothing else.

Story rules:
- Line 1 is a twisted hook: the most shocking moment of these chapters, told as a hook, then go back and tell the story from the start.
- Start mid-story. No intro, no greeting, no series name, no outro.
- Present tense, plain everyday words, no hype words. Each line is one sentence of 8 to 15 words.
- Wrap exactly one important word per line in stars, like *dead*.
- Use the real character names from the text (this is a manhwa). Never invent facts the panel text does not support.
- {SEGMENTS[0]} to {SEGMENTS[1]} lines, {WORDS[0]} to {WORDS[1]} words in total (a 3 to 8 minute video).
- The last line is a hard cliffhanger. Stop right at the moment before the answer. No outro, no "follow for more".

Pictures:
- Each line has "clips": panel seconds, taken from the panel list ("t=" numbers), in story order and matching what the line says.
- A clip is a number t, or [t, word_index] to start that picture on a given word of the line (counting words from 0).
- Use 1 to 3 clips per line. Every t must be a multiple of {STEP} between 0 and the last panel second given. Never invent a t.

JSON shape:
{{"title": "the story in one line, no series name", "caption_hook": "one hook sentence, may end with an emoji",
 "comment": "one question to the viewer, ends with an emoji", "mood": "calm|suspense|scifi|action|dark",
 "segments": [{{"say": "...", "clips": [12, [15, 4]]}}]}}"""


def api_key() -> str:
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"].strip()
    env = Path.home() / ".config" / "watch" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("no GROQ_API_KEY in the environment or ~/.config/watch/.env")


def panel_listing(panels: list[dict]) -> str:
    """'t=12: text' per panel with words, cut so the whole list fits the token budget."""
    withtext = [p for p in panels if len(p["text"].strip()) > 2]
    cap = max(40, min(160, TEXT_BUDGET // max(len(withtext), 1)))
    lines = [f"t={p['t']}: {p['text'].strip()[:cap]}" for p in withtext]
    while len("\n".join(lines)) > TEXT_BUDGET:  # still too long: keep an even spread of panels
        lines = lines[::2] + ([lines[-1]] if len(lines) % 2 == 0 else [])
    return "\n".join(lines)


def previous_lines(job: Path, part: int) -> list[str]:
    prev = job.with_name(re.sub(r"-p\d+$", f"-p{part - 1}", job.name))
    path = prev / "script.json"
    if part < 2 or not path.exists():
        return []
    segments = json.loads(path.read_text(encoding="utf-8"))["segments"]
    return [s["say"] for s in segments][-15:]


def build_prompt(job: Path, meta: dict, panels: list[dict]) -> str:
    last_t = max(p["t"] for p in panels)
    parts = [f"Series: {meta['title']} (also called: {', '.join(meta['alt_titles']) or 'no other names'})",
             f"About: {meta['description'][:500]}",
             f"This is part {meta['part']}, chapters {meta['chapters']}. Last panel second: {last_t}. "
             "Panels with no words are left out of the list; their seconds are still valid picture times."]
    before = previous_lines(job, meta["part"])
    if before:
        parts.append("The previous part ended with these lines (continue the story, do not repeat them):\n" + "\n".join(before))
    parts.append("Panel text, in story order:\n" + panel_listing(panels))
    return "\n\n".join(parts)


def validate(data: dict, last_t: int) -> list[str]:
    """Every problem found, as short sentences the model can act on. Empty list = good."""
    errors: list[str] = []
    for key in ("title", "caption_hook", "comment", "mood"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f'missing text field "{key}"')
    segs = data.get("segments")
    if not isinstance(segs, list):
        return errors + ['"segments" must be a list']
    if not SEGMENTS[0] <= len(segs) <= SEGMENTS[1]:
        errors.append(f"{len(segs)} segments, need {SEGMENTS[0]} to {SEGMENTS[1]}")
    total = 0
    for i, seg in enumerate(segs):
        say, clips = seg.get("say") if isinstance(seg, dict) else None, seg.get("clips") if isinstance(seg, dict) else None
        if not isinstance(say, str) or not isinstance(clips, list) or not clips:
            errors.append(f"segment {i}: needs a string say and a non-empty clips list")
            continue
        words = len(say.split())
        total += words
        if not LINE_WORDS[0] <= words <= LINE_WORDS[1]:
            errors.append(f"segment {i}: {words} words, a line is 8 to 15 words")
        if not re.search(r"\*[^*\s][^*]*\*", say):
            errors.append(f"segment {i}: no *keyword* in stars")
        for clip in clips:
            t, idx = (clip[0], clip[1]) if isinstance(clip, list) and len(clip) == 2 else (clip, None)
            if not isinstance(t, int) or isinstance(t, bool) or t % STEP or not 0 <= t <= last_t:
                errors.append(f"segment {i}: clip {clip!r}, t must be a multiple of {STEP} from 0 to {last_t}")
            elif idx is not None and (not isinstance(idx, int) or not 0 <= idx < words):
                errors.append(f"segment {i}: clip {clip!r}, word_index must be below {words}")
    if segs and not WORDS[0] <= total <= WORDS[1]:
        errors.append(f"{total} words in total, need {WORDS[0]} to {WORDS[1]}")
    return errors[:12]


def ask(key: str, messages: list[dict], json_mode: bool = True) -> str:
    body = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_completion_tokens": ANSWER_TOKENS,
            "reasoning_effort": "low"}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(URL, json.dumps(body).encode(), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "recap-autopilot/1"})
    for wait in (0, 30, 65):  # the token limit is per minute: wait and ask again
        time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.load(resp)["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            if exc.code == 429:
                print(f"Groq rate limit, waiting: {detail[:120]}", file=sys.stderr, flush=True)
                continue
            if exc.code == 400 and json_mode:  # model without JSON mode: ask in plain mode
                return ask(key, messages, json_mode=False)
            sys.exit(f"Groq HTTP {exc.code}: {detail}")
    sys.exit("Groq rate limit did not clear")


def parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def write_draft(job: Path, meta: dict, panels: list[dict]) -> dict:
    key = api_key()
    last_t = max(p["t"] for p in panels)
    messages = [{"role": "system", "content": RULES}, {"role": "user", "content": build_prompt(job, meta, panels)}]
    for attempt in range(1, TRIES + 1):
        reply = ask(key, messages)
        try:
            data = parse(reply)
            errors = validate(data, last_t) if isinstance(data, dict) else ["the answer must be one JSON object"]
        except json.JSONDecodeError as exc:
            data, errors = None, [f"the answer was not valid JSON ({exc})"]
        if not errors:
            return data
        print(f"try {attempt}: {len(errors)} problems: {errors[:3]}", file=sys.stderr, flush=True)
        # a retry resends the prompt with the problems, not the old answer: both would not fit the token limit
        messages = messages[:2] + [{"role": "user", "content": "Your last answer had these problems, avoid them:\n- " + "\n- ".join(errors)}]
        time.sleep(60)  # the free tier counts tokens per minute
    sys.exit(f"no valid script after {TRIES} tries")


def assemble(draft: dict, meta: dict) -> dict:
    """The model's story plus the fixed fields of the hand-made example."""
    script = {k: v for k, v in json.loads(EXAMPLE.read_text(encoding="utf-8")).items() if k not in FIXED_DROP}
    tag = "#" + re.sub(r"[^a-z0-9]", "", meta["title"].lower())
    part = meta["part"]
    script.update(
        voice_chain=["kokoro", "edge"], kokoro_voice="am_liam", speed=1.0,
        music=MUSIC_SUSPENSE if draft["mood"].strip().lower() in {"suspense", "scifi"} else MUSIC_DEFAULT,
        credit=meta["title"])
    return {"title": draft["title"].strip(),
            "caption": f"{draft['caption_hook'].strip()}\n\nPart {part}. Follow for Part {part + 1}!\n\n{tag} {HASHTAGS}",
            **script, "comment": draft["comment"].strip(), "segments": draft["segments"]}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    job = Path(sys.argv[1]).resolve()
    meta = json.loads((job / "meta.json").read_text(encoding="utf-8"))
    panels = json.loads((job / "work" / "panels_text.json").read_text(encoding="utf-8"))
    if not panels:
        sys.exit("panels_text.json is empty: OCR found no panels")
    script = assemble(write_draft(job, meta, panels), meta)
    (job / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{job.name}: {len(script['segments'])} segments, music {Path(script['music']).name}")


if __name__ == "__main__":
    main()
