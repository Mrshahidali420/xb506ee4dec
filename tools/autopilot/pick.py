"""Pick the next video for the autopilot and create its job folder.

Usage: python pick.py manhwa        (prints the job folder, e.g. jobs/auto-solo-leveling-p1)

An open series (autopilot/series.json) is continued first: the next 5 chapters, part N+1.
Otherwise AniList gives lesser-known Korean manhwa (popularity 2k-40k, score 72+, 60+ chapters or still
running); the first one that find_manhwa.py can find a download source for is started at chapter 1.
Titles listed in autopilot/done.json are never picked. Writes jobs/auto-<slug>-p<N>/source.json + meta.json.
Log lines go to stderr, so stdout is only the job path.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import find_manhwa  # noqa: E402  (needs the path line above)

STATE = ROOT / "autopilot"
CHAPTERS_PER_PART = 5
MAX_ATTEMPTS = 3        # picks of one part without a queued video before the series is dropped
CANDIDATE_TRIES = 8     # new titles checked for sources per run
QUERY = """query($page:Int){Page(page:$page,perPage:50){media(type:MANGA,format:MANGA,countryOfOrigin:KR,
isAdult:false,popularity_greater:2000,popularity_lesser:40000,averageScore_greater:71,sort:POPULARITY_DESC){
id title{romaji english} synonyms description(asHtml:false) chapters status averageScore popularity}}}"""


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load(name: str, default):
    path = STATE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(name: str, data) -> None:
    STATE.mkdir(exist_ok=True)
    (STATE / name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower().replace("'", "")).strip("-")[:40].strip("-")


def display_title(media: dict) -> str:
    t = media["title"]
    return (t.get("english") or t.get("romaji") or "").strip()


def candidates(done: set[str], running: set[str]) -> list[dict]:
    """AniList manhwa that fit the rules, in random order so runs do not repeat."""
    out: list[dict] = []
    for page in (1, 2):
        resp = requests.post("https://graphql.anilist.co", json={"query": QUERY, "variables": {"page": page}}, timeout=30)
        resp.raise_for_status()
        for m in resp.json()["data"]["Page"]["media"]:
            title = display_title(m)
            slug = slugify(title)
            long_enough = (m["chapters"] or 0) >= 60 or m["status"] == "RELEASING"
            if title.isascii() and slug and long_enough and slug not in done and slug not in running:
                out.append(m)
    random.shuffle(out)
    return out


def find_sources(title: str) -> list[str]:
    """Best download source of each site, under every name AniList knows (find_manhwa's own search)."""
    found: dict[str, list[str]] = {s: [] for s in find_manhwa.ORDER}
    for name in find_manhwa.names(title):
        for site in find_manhwa.ORDER:
            try:
                for url in find_manhwa.SEARCH[site](name):
                    if url not in found[site]:
                        found[site].append(url)
            except Exception as exc:
                log(f"{site} '{name}': {str(exc)[:100]}")
    return [u for s in find_manhwa.ORDER for u in found[s][:1]]


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def write_job(slug: str, entry: dict, alt_titles: list[str], description: str) -> str:
    part, first = entry["part"], entry["next_chapter"]
    last = first + CHAPTERS_PER_PART - 1
    if entry.get("total"):
        last = min(last, entry["total"])
    job = ROOT / "jobs" / f"auto-{slug}-p{part}"
    job.mkdir(parents=True, exist_ok=True)
    chapters = f"{first}-{last}"
    (job / "source.json").write_text(json.dumps(
        {"type": "manga", "chapters": chapters, "sources": entry["sources"]}, indent=1) + "\n", encoding="utf-8")
    (job / "meta.json").write_text(json.dumps(
        {"title": entry["title"], "alt_titles": alt_titles, "description": description, "part": part,
         "chapters": chapters}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"jobs/{job.name}"


def anilist_info(title: str) -> tuple[list[str], str]:
    q = "query($s:String){Media(search:$s,type:MANGA){synonyms description(asHtml:false)}}"
    try:
        m = requests.post("https://graphql.anilist.co", json={"query": q, "variables": {"s": title}},
                          timeout=30).json()["data"]["Media"]
        return [s for s in m["synonyms"] if s.isascii()][:5], clean(m["description"])
    except Exception as exc:
        log(f"AniList info: {exc}")
        return [], ""


def continue_series(series: dict, done: set[str]) -> str | None:
    """The first open series that still has chapters; a series that keeps failing is dropped."""
    for slug, entry in list(series.items()):
        entry["attempts"] = entry.get("attempts", 0) + 1
        finished = entry.get("total") and entry["next_chapter"] > entry["total"]
        if finished or entry["attempts"] > MAX_ATTEMPTS:
            log(f"{slug}: {'finished' if finished else f'{MAX_ATTEMPTS} failed tries'}, closed")
            done.add(slug)
            del series[slug]
            continue
        alt, desc = anilist_info(entry["title"])
        return write_job(slug, entry, alt, desc)
    return None


def pick_manhwa() -> str | None:
    series = load("series.json", {})
    done = set(load("done.json", []))
    try:
        job = continue_series(series, done)
        if job:
            return job
        for media in candidates(done, set(series))[:CANDIDATE_TRIES]:
            title = display_title(media)
            log(f"candidate: {title} (popularity {media['popularity']}, score {media['averageScore']})")
            sources = find_sources(title)
            if not sources:
                log("  no sources, skipped")
                continue
            slug = slugify(title)
            series[slug] = {"title": title, "sources": sources, "next_chapter": 1, "part": 1, "attempts": 1,
                            "total": media["chapters"] or None}
            return write_job(slug, series[slug], [s for s in media["synonyms"] if s.isascii()][:5],
                             clean(media["description"]))
        return None
    finally:  # state is saved on every exit, including a crash after attempts were counted
        save("series.json", series)
        save("done.json", sorted(done))


def main() -> None:
    if sys.argv[1:] != ["manhwa"]:
        sys.exit(__doc__)
    job = pick_manhwa()
    if not job:
        sys.exit("no manhwa with a download source found")
    print(job)


if __name__ == "__main__":
    main()
