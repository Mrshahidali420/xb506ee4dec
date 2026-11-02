"""Find the original movie / drama / anime behind a reference clip.

Usage: uv run -q --python 3.11 --with pillow --with numpy --with requests --with truststore python tools/find_show.py refs/<name>
Needs refs/<name>/video.mp4 and transcript.txt (tools/study_ref.py makes both) and GEMINI_API_KEY in the env or
~/.config/watch/.env (never printed; on 2 Nov 2026 that key was EMPTY, so the Groq+DuckDuckGo fallback runs).

Method (chosen after comparing options, see REF-STUDY.md step 4):
  1. Pick 8 clear frames (sharp, well lit, contrast, spread over the clip; black/blank/text-only frames score low).
  2. trace.moe (free, no key): anime only; counts only when similarity >= 0.90.
  3. Gemini vision + google_search grounding: the 8 frames plus the transcript (names, places, plot) go in one call;
     it must name the title, year, country, confidence and evidence. This is the live-action method.
  Reverse image search (Yandex/Bing/Google Lens) was rejected: no free key-less API, scrapers break behind captchas.
Writes refs/<name>/find_show.json and prints the top guesses. A guess below 0.6 confidence stays "unsure".
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import requests
import truststore
from PIL import Image

truststore.inject_into_ssl()

N_FRAMES = 8
CANDIDATE_EVERY = 4        # seconds between candidate frames
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash"]
ANIME_OK = 0.90            # trace.moe similarity that counts as a match
SURE = 0.6


def find_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        return
    out = subprocess.run(["powershell", "-c", "[Environment]::GetEnvironmentVariable('Path','User')"],
                         capture_output=True, text=True).stdout
    for p in out.strip().split(";"):
        if "ffmpeg" in p.lower() and Path(p.strip(), "ffmpeg.exe").exists():
            os.environ["PATH"] = p.strip() + os.pathsep + os.environ["PATH"]
            return
    sys.exit("ffmpeg not found")


def gemini_key_or_none() -> str:
    env = Path.home() / ".config" / "watch" / ".env"
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def gemini_key() -> str:
    if os.environ.get("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    env = Path.home() / ".config" / "watch" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("GEMINI_API_KEY not found")


def score(path: Path) -> float:
    """Sharp + lit + contrasty frames win; black, flat and text-card frames lose."""
    with Image.open(path) as im:
        g = np.asarray(im.convert("L").resize((240, 135)), dtype=float)
    if g.mean() < 25 or g.std() < 18:
        return 0.0
    gy, gx = np.gradient(g)
    sharp = float(np.hypot(gx, gy).mean())
    return sharp * min(g.std() / 50, 1.5)


def pick_frames(video: Path, out: Path) -> list[Path]:
    out.mkdir(exist_ok=True)
    tmp = out / "_cand"
    tmp.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", f"fps=1/{CANDIDATE_EVERY},scale=640:-2",
                    str(tmp / "c_%04d.jpg")], check=True)
    cands = sorted(tmp.glob("c_*.jpg"))
    # one winner per equal time bin so the 8 frames cover the whole clip
    chosen = []
    for b in np.array_split(np.arange(len(cands)), N_FRAMES):
        if len(b) == 0:
            continue
        best = max(b, key=lambda i: score(cands[i]))
        dst = out / f"f_{best * CANDIDATE_EVERY:04d}s.jpg"
        shutil.copy(cands[best], dst)
        chosen.append(dst)
    shutil.rmtree(tmp)
    return chosen


def trace_moe(frames: list[Path]) -> list[dict]:
    hits = []
    for f in frames:
        try:
            r = requests.post("https://api.trace.moe/search?anilistInfo&cutBorders", data=f.read_bytes(),
                              headers={"Content-Type": "image/jpeg"}, timeout=60)
            res = r.json().get("result", [])
        except Exception as exc:
            print(f"trace.moe {f.name}: {exc}", file=sys.stderr)
            continue
        if res and res[0]["similarity"] >= ANIME_OK:
            t = res[0]
            title = (t.get("anilist") or {}).get("title", {})
            hits.append({"frame": f.name, "similarity": round(t["similarity"], 3),
                         "title": title.get("english") or title.get("romaji") or str(t.get("anilist")),
                         "episode": t.get("episode"), "at_s": round(t.get("from", 0))})
    return hits


def transcript_hint(d: Path) -> str:
    txt = (d / "transcript.txt").read_text(encoding="utf-8") if (d / "transcript.txt").exists() else ""
    plain = re.sub(r"\[\d+:\d+\]\s*", "", txt)
    names = sorted(set(re.findall(r"(?<=[a-z,] )[A-Z][a-z]{2,}\b", plain)))
    music = (d / "music.txt").read_text(encoding="utf-8").strip() if (d / "music.txt").exists() else ""
    return (f"Names/places that appear capitalised: {', '.join(names[:40])}\nMusic ids: {music}\n"
            f"Transcript (English voice-over, names may be changed):\n{plain[:3500]}")


PROMPT = """These {n} frames come from one short recap clip of an ORIGINAL movie, TV drama or anime. A narrator re-tells it, so the
names in the voice-over may be localised or invented. Identify the original work. Use Google Search to check candidates
against the plot, cast and setting. Reply with ONLY a JSON object:
{{"guesses":[{{"title":"","original_title":"","year":"","country":"","type":"film|series|anime","confidence":0.0,
"evidence":"short, concrete: which plot points, actors, locations or on-screen text matched","episode_or_scene_hint":""}}],
"language_of_original":"","notes":""}}
Give up to 3 guesses ordered by confidence (0-1). If you cannot find a match backed by search results, return a low
confidence and say so; never invent a title.

{hint}"""


def gemini(frames: list[Path], hint: str) -> dict:
    parts = [{"text": PROMPT.format(n=len(frames), hint=hint)}]
    for f in frames:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f.read_bytes()).decode()}})
    body = {"contents": [{"parts": parts}], "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0.2}}
    last = ""
    for model in GEMINI_MODELS:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                          headers={"x-goog-api-key": gemini_key()}, json=body, timeout=180)
        if r.status_code != 200:
            last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
            print(last, file=sys.stderr)
            continue
        cand = r.json()["candidates"][0]
        text = "".join(p.get("text", "") for p in cand["content"]["parts"])
        m = re.search(r"\{.*\}", text, re.S)
        sources = [c["web"]["title"] for c in cand.get("groundingMetadata", {}).get("groundingChunks", []) if "web" in c]
        try:
            out = json.loads(m.group(0)) if m else {"raw": text}
        except json.JSONDecodeError:
            out = {"raw": text}
        out["model"] = model
        out["search_sources"] = sources[:8]
        return out
    return {"error": last}


GROQ_TEXT = "openai/gpt-oss-120b"


def groq_key() -> str:
    env = Path.home() / ".config" / "watch" / ".env"
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("GROQ_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    sys.exit("GROQ_API_KEY not found")


def groq(model: str, content: list | str, max_tokens: int = 900) -> str:
    r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                      headers={"Authorization": f"Bearer {groq_key()}"},
                      json={"model": model, "messages": [{"role": "user", "content": content}],
                            "temperature": 0.2, "max_completion_tokens": max_tokens + 3000}, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def web_search(query: str) -> list[dict]:
    """DuckDuckGo html endpoint: free, no key."""
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                          headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    except Exception as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return []
    out = []
    for m in re.finditer(r'class="result__a"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', r.text, re.S):
        out.append({"title": re.sub("<.*?>", "", m.group(1)).strip(), "snippet": re.sub("<.*?>", "", m.group(2)).strip()})
    return out[:6]


def groq_fallback(frames: list[Path], hint: str) -> dict:
    """No Gemini key: Groq text model writes search queries from the transcript (no Groq vision model exists any more); DuckDuckGo results are judged by Groq text."""
    ask = ("This text is the narration of a recap clip of a movie/drama. Write 3 short web "
           "search queries that would find the ORIGINAL title (plot, setting, country, visible on-screen text). "
           "Output only the 3 queries, one per line.\n\n" + hint[:2500])
    q = groq(GROQ_TEXT, ask, 300)
    queries = [x.strip(" -*0123456789.\"") for x in q.splitlines() if x.strip()][:3]
    found = {qq: web_search(qq) for qq in queries}
    prompt = ("Identify the original movie/drama/anime of a recap clip. Evidence from the clip:\n" + hint[:2500]
              + "\n\nWeb results:\n" + json.dumps(found, ensure_ascii=False)[:6000]
              + "\n\nReply with ONLY JSON: {\"guesses\":[{\"title\":\"\",\"original_title\":\"\",\"year\":\"\",\"country\":\"\","
              "\"confidence\":0.0,\"evidence\":\"which plot points matched which result\"}]}. Only titles that appear in "
              "the results; if none fit, one guess with confidence under 0.3.")
    text = groq(GROQ_TEXT, prompt)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        out = json.loads(m.group(0)) if m else {"raw": text}
    except json.JSONDecodeError:
        out = {"raw": text}
    for x in out.get("guesses", []):  # title-matching on snippets is weak (cry-ref1 matched a documentary): never "sure"
        x["confidence"] = min(float(x.get("confidence", 0)), 0.5)
        x["evidence"] = "FALLBACK, verify by hand: " + str(x.get("evidence", ""))
    out.update({"model": f"ddg+{GROQ_TEXT} (text only: Groq has no vision model on 2 Nov 2026)", "queries": queries})
    return out


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = Path(sys.argv[1]).resolve()
    find_ffmpeg()
    frames = pick_frames(d / "video.mp4", d / "find_show_frames")
    print(f"frames: {[f.name for f in frames]}")
    anime = trace_moe(frames)
    print(f"trace.moe confident hits: {len(anime)}")
    hint = transcript_hint(d)
    g = gemini(frames, hint) if gemini_key_or_none() else groq_fallback(frames, hint)
    result = {"frames": [f.name for f in frames], "trace_moe": anime, "gemini": g}
    (d / "find_show.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for a in anime[:3]:
        print(f"ANIME  {a['title']} ep {a['episode']} @ {a['at_s']}s  sim {a['similarity']}  ({a['frame']})")
    for x in g.get("guesses", []):
        tag = "" if float(x.get("confidence", 0)) >= SURE else "  [UNSURE]"
        print(f"GUESS  {x.get('title')} ({x.get('original_title')}, {x.get('year')}, {x.get('country')}) "
              f"conf {x.get('confidence')}{tag}\n       {x.get('evidence')}")
    if "guesses" not in g:
        print("no structured answer:", json.dumps(g)[:400])
    print(f"wrote {d / 'find_show.json'}")


if __name__ == "__main__":
    main()
