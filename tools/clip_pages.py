"""The three clip-reel Pages that pick their own random good title (Anime Rewind, Manhwa X, Spoiler Room).

Used by tools/clip_daily.py (`pick <page>`) and tools/post_clips.py (the Page settings). No AI, no browser.
Each Page: a few tiers with weights; a tier is drawn at random, and inside it a random usable title/episode.
Never the same show twice in a row, never a key already done or failed twice, and not a show the rival Page
clipped in the last 3 days (Anime Rewind vs WhereAnime, Manhwa X vs Manhwa Index). Nothing here posts.
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAN_DAYS = 3
MAX_TRIES = 2
BEST_SCORE = 78
BEST_POPULARITY = 50000
EP_CAP = 12          # best-of: a random episode among the first 12
AUDIO_WEIGHTS = {"dub": 1, "sub": 1}   # Anime Rewind and Manhwa X: English dub or original sub, about half each (a title with no dub falls back to sub)
BEST_QUERY = """query($page:Int,$min:Int,$pop:Int){Page(page:$page,perPage:50){media(type:ANIME,format_in:[TV,ONA],isAdult:false,averageScore_greater:$min,
popularity_greater:$pop,sort:POPULARITY_DESC){id title{romaji english} episodes trending popularity averageScore
relations{edges{relationType node{id type countryOfOrigin}}}}}}"""

# queue = the recap queue file to look for our own video; fb = prefix of the <X>_FB_PAGE_ID/_TOKEN secrets; rival = the Page to keep away from
PAGES = {
    "animerewind": {"short": "ar", "label": "anime", "queue": "anime", "fb": "ANIME", "rival": "whereanime"},
    "manhwax": {"short": "mx", "label": "manhwa adaptation", "queue": "manhwax", "fb": "MANHWAX", "rival": "manhwaindex"},
    "spoiler": {"short": "sp", "label": "drama and movie", "queue": "spoiler", "fb": "SPOILER", "rival": None},
}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cand(key: str, show, title: str, ep: int, source: dict, weight: float = 1.0, **extra) -> dict:
    return {"key": key, "show": show, "title": title, "episode": ep, "source": source, "w": weight,
            "anilist_id": None, "manga_id": None, "link": None, "movie": False, **extra}


def anime_cand(c: dict, need_manga: bool) -> dict | None:
    """An airing (or best-of) episode as a page candidate; None when the Page needs a manhwa adaptation and it is not one."""
    if need_manga and not c["manga_id"]:
        return None
    link = f"https://anilist.co/manga/{c['manga_id']}" if need_manga else f"https://anilist.co/anime/{c['anilist_id']}"
    audio = random.choices(list(AUDIO_WEIGHTS), weights=list(AUDIO_WEIGHTS.values()))[0]
    source = {"type": "anime", "title": c["title"], "alt": [c["romaji"]] if c.get("romaji") else [], "episode": c["episode"], "lang": audio}
    return cand(c["key"], c["anilist_id"], c["title"], c["episode"], source,
                1 + math.sqrt(c["popularity"]) / 100, anilist_id=c["anilist_id"], manga_id=c["manga_id"], link=link, audio=audio)


def airing_pool(cd, now: int, hours: int, need_manga: bool, good) -> list[dict]:
    return [p for c in cd.airing(now, hours) if good(c) and (p := anime_cand(c, need_manga))]


def best_pool(cd, now: int, need_manga: bool) -> list[dict]:
    """Well-scored, popular shows of any season: each of the first 12 episodes is a candidate; episode 1 and a season finale weigh more."""
    minimum, popularity = (70, 20000) if need_manga else (BEST_SCORE, BEST_POPULARITY)
    body = json.dumps({"query": BEST_QUERY, "variables": {"page": random.choice([1, 2, 3]), "min": minimum, "pop": popularity}}).encode()
    req = urllib.request.Request("https://graphql.anilist.co", body, {"Content-Type": "application/json", "User-Agent": "clip-daily"})
    media = json.loads(urllib.request.urlopen(req, timeout=60).read())["data"]["Page"]["media"]
    out = []
    for m in media:
        first = len(out)
        manga = cd.manhwa_id(m)
        title, last = m["title"]["english"] or m["title"]["romaji"], min(m["episodes"] or EP_CAP, EP_CAP)
        for ep in range(1, last + 1):
            p = anime_cand({"key": f"{m['id']}-{ep}", "anilist_id": m["id"], "episode": ep, "title": title, "manga_id": manga,
                            "romaji": m["title"]["romaji"], "popularity": m["popularity"] or 0}, need_manga)
            if p:
                p["w"] *= 3 if ep == 1 or ep == m["episodes"] else 1
                out.append(p)
        for p in out[first:]:   # a show is picked by its own weight, not once per episode
            p["w"] /= len(out) - first
    return out


def spoiler_pool(kind: str) -> list[dict]:
    """Episodes of the K-drama series, or the K-movies, in autopilot/spoiler-pool.json (copied from daily/plan.json "spoiler", which is not in git)."""
    pool = json.loads((ROOT / "autopilot" / "spoiler-pool.json").read_text(encoding="utf-8"))
    out = []
    if kind == "series":
        for s in pool["series"]:
            for n in range(1, s["last"] + 1):   # one drama weighs the same as another, whatever its length
                out.append(cand(f"spoiler:{s['name']}-e{n}", s["name"], s["name"].replace("-", " ").title(), n,
                                {"type": "kisskh", "url": s["url"], "episode": n}, 1 / s["last"]))
    else:
        for t in pool["movies"]:
            out.append(cand(f"spoiler:movie-{slug(t)}", slug(t), t, 1, {"type": "kisskh", "search": t, "episode": 1}, movie=True))
    import clip_daily as cd
    spent = set(cd.state("spoiler")["done"])
    left = sum(1 for c in out if c["key"] not in spent)
    if left < 5:
        print(f"::warning::spoiler pool ({kind}) has only {left} unused title(s): add more to autopilot/spoiler-pool.json")
    return out


def tiers(page: str, cd, now: int) -> list[tuple[float, object]]:
    """(weight, pool builder) for the Page."""
    if page == "animerewind":
        return [(3, lambda: airing_pool(cd, now, 48, False, lambda c: c["trending"] >= 10 or c["score"] >= 70 or c["popularity"] >= 20000)),
                (2, lambda: airing_pool(cd, now, 10 * 24, False, lambda c: c["trending"] >= 10 or c["score"] >= 75)),
                (4, lambda: best_pool(cd, now, False))]
    if page == "manhwax":
        return [(3, lambda: airing_pool(cd, now, 10 * 24, True, lambda c: c["trending"] >= 5 or c["score"] >= 70)),
                (2, lambda: best_pool(cd, now, True))]
    return [(1, lambda: spoiler_pool("series")), (1, lambda: spoiler_pool("movie"))]


def pick_page(page: str) -> None:
    import clip_daily as cd   # loaded here: clip_daily imports this file at the top

    conf, now = PAGES[page], int(time.time())
    st, main = cd.state(page), cd.state()
    last = st["shows"][-1]["show"] if st.get("shows") else None
    banned = {s["show"] for s in main.get("shows", []) if s["page"] == conf["rival"] and now - s["at"] < BAN_DAYS * 86400}
    seen = set(st["done"]) | (set(main["done"]) if page != "spoiler" else set())

    def usable(c: dict) -> bool:
        return c["key"] not in seen and st["failed"].get(c["key"], 0) < MAX_TRIES and c["show"] != last and c["show"] not in banned

    order, left = [], tiers(page, cd, now)
    while left:   # a weighted random order of the tiers; a tier with nothing usable hands over to the next
        t = random.choices(left, weights=[w for w, _ in left])[0]
        left.remove(t)
        order.append(t[1])
    chosen = None
    for build in order:
        try:
            pool = [c for c in build() if usable(c)]
        except Exception as err:  # a failing source (AniList down) must not stop the other tiers
            print(f"tier failed: {err}", file=sys.stderr)
            continue
        if pool:
            chosen = random.choices(pool, weights=[c["w"] for c in pool])[0]
            break
    if not chosen:
        print(f"::warning::{page}: nothing to clip now")
        return
    job = ROOT / "jobs" / f"clip-{conf['short']}-{slug(chosen['title'])}-e{chosen['episode']:02d}"
    job.mkdir(parents=True, exist_ok=True)
    (job / "source.json").write_text(json.dumps(chosen["source"], indent=1))
    (job / "clip.json").write_text(json.dumps({"kind": "clip", "pages": [page], "title": chosen["title"], "episode": chosen["episode"],
                                               "anilist_id": chosen["anilist_id"], "manga_id": chosen["manga_id"], "key": chosen["key"],
                                               "show": chosen["show"], "genre": conf["label"], "movie": chosen["movie"],
                                               "link": chosen["link"], "audio": chosen.get("audio"), "top": 1}, indent=1))
    print(f"picked {chosen['title']} episode {chosen['episode']} for {page}{' (' + chosen['audio'] + ')' if chosen.get('audio') else ''}", file=sys.stderr)
    print(f"job=jobs/{job.name}")
