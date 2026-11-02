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

Cascade (research and sources: refs/FIND-SHOW-RESEARCH.md), cheapest and most reliable first:
  0. Post caption / description / TikTok+YouTube comments (yt-dlp info.json): recap pages often write "Title/Credit - X".
     Facebook comments are not exposed by yt-dlp (0 returned on 2 Nov 2026).
  1. trace.moe (anime). 2. Gemini frames + caption + transcript + search. 3. If unsure, a plot-only search pass (no frames).
  4. VERIFY the top two guesses with a second search call; documentaries and plot mismatches are demoted.
  5. Only when the env var exists: TMDB_API_KEY (free) confirms the title and prints cast.
  NOT active (paid): SERPAPI_KEY (Google Lens, from $25/mo for 1000 searches), AWS_ACCESS_KEY_ID (Rekognition
  RecognizeCelebrities, about $0.001/image). Gemini will not name people from faces, so actors come from text and search.
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
import time
from pathlib import Path

import numpy as np
import requests
import truststore
from PIL import Image

truststore.inject_into_ssl()

N_FRAMES = 8
CANDIDATE_EVERY = 4        # seconds between candidate frames
GEMINI_PREFER = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-flash-latest"]  # tried first if listed
_models: list[str] = []
_state = {"exhausted": False}
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


def caption_hint(d: Path) -> str:
    """Post caption (hashtags stripped) and, where yt-dlp can read them, the top comments. Recap pages often name the show."""
    try:
        info = json.loads((d / "info.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    text = re.sub(r"#\S+", "", info.get("description") or "").strip()
    comments = info.get("comments") or fetch_comments(info.get("webpage_url", ""), d)
    top = [c.get("text", "")[:160] for c in sorted(comments, key=lambda c: -(c.get("like_count") or 0))[:15]]
    out = f"Post uploader: {info.get('uploader')}\nPost caption: {text}\n"
    return out + ("Top comments:\n- " + "\n- ".join(top) + "\n" if top else "")


def fetch_comments(url: str, d: Path) -> list[dict]:
    """TikTok / YouTube only: yt-dlp returns no comments for Facebook (tested 2 Nov 2026)."""
    if not re.search(r"tiktok\.com|youtube\.com|youtu\.be", url):
        return []
    tmp = d / "_comments"
    r = subprocess.run(["yt-dlp", "--skip-download", "--write-comments", "--write-info-json", "-o", str(tmp / "c"), url],
                       capture_output=True, text=True)
    try:
        return json.loads(next(tmp.glob("*.info.json")).read_text(encoding="utf-8")).get("comments") or []
    except (StopIteration, OSError, json.JSONDecodeError):
        print(f"comments: none ({r.stderr[-120:].strip()})", file=sys.stderr)
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PROMPT = """These {n} frames come from one short recap clip of an ORIGINAL movie, TV drama or anime (K-drama, Chinese or short
drama, Hollywood, Japanese, Thai, Indian...). The clip may be English-dubbed with RENAMED characters, cropped, mirrored,
zoomed, with captions and watermarks, so judge by the story, setting, era, props and any on-screen text, not by the
voice-over names. Read every caption, logo and watermark in the frames. Search Google (also in Korean/Chinese/Japanese
if the setting suggests it) for candidates and check each against the plot, cast and setting. The original is a scripted
film or series: reject documentaries, news and reality shows. Do not name anyone from their face; take actor names only
from text or search results. Reply with ONLY a JSON object:
{{"guesses":[{{"title":"","original_title":"","year":"","country":"","type":"film|series|anime","confidence":0.0,
"actors":[""],"evidence":"concrete: plot points, locations, on-screen text, search result that matched","episode_or_scene_hint":""}}],
"language_of_original":"","notes":""}}
Up to 3 guesses ordered by confidence (0-1). No match backed by search results means low confidence; never invent a title.

{hint}"""

PLOT_PROMPT = """Below is the narration of a recap clip of a scripted movie or TV drama (characters may be renamed) and the
post caption. Search Google for the original: try the plot in English, Korean and Chinese and check MyDramaList, TMDb,
Wikipedia, AsianWiki and Douban. Dramas of any year count (2010-2025 as much as 2026); do not favour new releases.
Give up to 5 candidates ranked by how well the premise fits; a weak fit is still listed, with low confidence, so it can be
checked. Reject documentaries and news. Reply with ONLY JSON:
{{"guesses":[{{"title":"","original_title":"","year":"","country":"","type":"","confidence":0.0,"actors":[""],"evidence":""}}]}}

{hint}"""

VERIFY_PROMPT = """Check one candidate. Candidate: "{title}" ({original}, {year}, {country}). Search for its official synopsis and
cast, then compare with this clip summary. Is it a scripted film/series whose plot fits the summary? The voice-over
names are invented by the dubber: NEVER count a name difference as a contradiction. A plot point missing from a short
synopsis is not a contradiction either; only a clear conflict is (wrong genre, gender, era, country, or an event the
synopsis rules out). Anthology or multi-case series: check each case/episode title and say which one fits. If the title is shared by several productions, find the one that fits (country, year, cast) and name it. Reply
with ONLY JSON: {{"match":true,"is_documentary_or_news":false,"matched_points":[""],"contradictions":[""],
"synopsis_source":"url","confidence":0.0,"best_title":"","original_title":"","year":"","country":"","episode_hint":""}}

Clip summary:
{hint}"""


def gemini_models() -> list[str]:
    """Ask the API which models this key can call (hard-coded names go dead: gemini-2.0-flash 404'd on 2 Nov 2026)."""
    if not _models:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=100",
                         headers={"x-goog-api-key": gemini_key()}, timeout=30).json()
        ok = [m["name"].split("/")[-1] for m in r.get("models", [])
              if "generateContent" in m.get("supportedGenerationMethods", [])
              and re.fullmatch(r"gemini-[\d.]+-flash(-preview)?|gemini-flash-latest", m["name"].split("/")[-1])]
        _models.extend([m for m in GEMINI_PREFER if m in ok] + sorted(m for m in ok if m not in GEMINI_PREFER))
    return _models


def gemini_call(parts: list) -> dict:
    """Walk the working flash models: 429 (no free quota) or 404 skips a model, 503 (busy) is retried twice."""
    body = {"contents": [{"parts": parts}], "tools": [{"google_search": {}}], "generationConfig": {"temperature": 0.2}}
    last = ""
    if _state["exhausted"]:
        return {"error": "all Gemini models out of quota or busy this run"}
    for model in gemini_models():
        for attempt in range(3):
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers={"x-goog-api-key": gemini_key()}, json=body, timeout=180)
            if r.status_code != 503:
                break
            time.sleep(6 * (attempt + 1))
        if r.status_code != 200:
            last = f"{model}: HTTP {r.status_code} {r.text[:120]}"
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
    _state["exhausted"] = True  # every model failed: stop spending calls on the verify steps
    return {"error": last}


def gemini(frames: list[Path], hint: str) -> dict:
    parts = [{"text": PROMPT.format(n=len(frames), hint=hint)}]
    for f in frames:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(f.read_bytes()).decode()}})
    return gemini_call(parts)


def gemini_plot(hint: str) -> dict:
    return gemini_call([{"text": PLOT_PROMPT.format(hint=hint)}])


def verify(guess: dict, hint: str) -> dict:
    """Second, independent search call on a guess; a documentary or a plot mismatch demotes it."""
    q = VERIFY_PROMPT.format(title=guess.get("title"), original=guess.get("original_title"), year=guess.get("year"),
                             country=guess.get("country"), hint=hint[:3000])
    v = gemini_call([{"text": q}])
    if v.get("error"):
        guess["verified"] = {"error": v["error"][:100]}
        guess["confidence"] = min(float(guess.get("confidence") or 0), 0.5)  # never "sure" without the check
        guess["evidence"] = "UNVERIFIED (verify call failed), check by hand: " + str(guess.get("evidence", ""))
        return guess
    conf = float(guess.get("confidence") or 0)
    if v.get("is_documentary_or_news") or v.get("match") is False:
        guess["confidence"] = min(conf, 0.3)
    elif v.get("match"):
        guess["confidence"] = max(conf, 0.8 if float(v.get("confidence") or 0) >= 0.6 else 0.5)
    if v.get("match") and v.get("original_title"):
        guess.update({k: v[k] for k in ("original_title", "year", "country") if v.get(k)})
    guess["verified"] = {k: v.get(k) for k in ("match", "matched_points", "contradictions", "synopsis_source", "best_title", "episode_hint")}
    return guess


def tmdb_check(title: str) -> dict:
    """Optional: runs only with TMDB_API_KEY (free key at themoviedb.org). Confirms the title exists and lists billed cast."""
    key = os.environ.get("TMDB_API_KEY")
    if not key or not title:
        return {}
    r = requests.get("https://api.themoviedb.org/3/search/multi", params={"api_key": key, "query": title}, timeout=30).json()
    hit = next((x for x in r.get("results", []) if x.get("media_type") in ("movie", "tv")), None)
    if not hit:
        return {"found": False}
    kind = hit["media_type"]
    cast = requests.get(f"https://api.themoviedb.org/3/{kind}/{hit['id']}/credits", params={"api_key": key},
                        timeout=30).json().get("cast", [])[:6]
    return {"found": True, "tmdb": f"{kind}/{hit['id']}", "overview": hit.get("overview", "")[:200],
            "cast": [c["name"] for c in cast]}


def caption_title(cap: str) -> str:
    """'Title/Credit - The truth', 'Movie: X', 'Name of drama - X': recap pages often state it. Returns '' if absent."""
    m = re.search(r"(?:title|credit|movie|drama|film|series|name)[^\n:\-]{0,12}[:\-]+\s*([^\n]{2,60})", cap, re.I)
    return re.sub(r"^[\W_]+|[\W_]+$", "", m.group(1)) if m else ""


def identify(frames: list[Path], hint: str) -> dict:
    """Gemini frames pass, then a plot-only pass if unsure, then verify the top two."""
    g = gemini(frames, hint)
    ct = caption_title(hint)
    if ct and not any(ct.lower() in str(x.get("title", "")).lower() for x in g.get("guesses", [])):  # strong lead: a bare caption title is often ambiguous ("The truth"), so it is verified like any guess
        g.setdefault("guesses", []).append({"title": ct, "confidence": 0.55, "evidence": "named in the post caption"})
    best = max(g.get("guesses", []), key=lambda x: float(x.get("confidence") or 0), default={})
    if float(best.get("confidence") or 0) < SURE:
        g["plot_pass"] = gemini_plot(hint)
        g["guesses"] = g.get("guesses", []) + g["plot_pass"].get("guesses", [])
    g["guesses"] = sorted((x for x in g.get("guesses", []) if x.get("title")),
                          key=lambda x: -float(x.get("confidence") or 0))
    for x in g["guesses"][:5]:
        verify(x, hint)
        x["tmdb"] = tmdb_check(x["title"])
    g["guesses"].sort(key=lambda x: -float(x.get("confidence") or 0))
    return g


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
    cap = caption_hint(d)
    hint = cap + transcript_hint(d)
    g = identify(frames, hint) if gemini_key_or_none() else groq_fallback(frames, hint)
    result = {"frames": [f.name for f in frames], "caption": cap, "trace_moe": anime, "gemini": g}
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
