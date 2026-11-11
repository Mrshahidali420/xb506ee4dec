"""Choose today's trending episode to clip, for the two Pages. No AI, no browser.

  python tools/clip_daily.py pick [page]     makes jobs/clip-<slug>-e<NN>/ (source.json, clip.json) and prints job=<folder> for GitHub
  python tools/clip_daily.py mark <job> done|fail    records the result in the state file
No page = the WhereAnime / Manhwa Index pick below. A page (animerewind, manhwax, spoiler) = one random good title for that
Page (tools/clip_pages.py), with its own state file autopilot/clips-state-<page>.json.

How it picks: AniList lists every episode that aired in the last 48 hours. The one whose show is trending
the most (then the most popular) wins, unless it was clipped already or failed twice. WhereAnime takes any
show. Manhwa Index takes only a show adapted from a manhwa or manhua (its source work comes from Korea or China;
the post links that comic's page), so the same episode may go to both Pages: one job, two logo copies.
Nothing here posts. clips/state.json is the memory, committed by the workflow.
"""
from __future__ import annotations

import json
import random
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import clip_pages  # noqa: E402  (the Pages with their own pick)

STATE = ROOT / "autopilot" / "clips-state.json"
WINDOW_HOURS = 15 * 24      # a fresh episode first, then the last 10 days, then good older ones up to 15 days
FRESH_HOURS = 72
RECENT_HOURS = 10 * 24
GOOD_SCORE = 75
PAGES = 8
MIN_AGE_HOURS = 3          # a fresh release needs time to reach the stream site
MAX_TRIES = 1          # an episode the stream site lacks fails fast and is skipped; the pool is large
PER_DAY = int(os.environ.get("CLIPS_PER_DAY", "99"))
FORMATS = {"TV", "TV_SHORT", "ONA"}
KOREA_CHINA = {"KR", "CN"}
QUERY = """query($from:Int,$to:Int,$page:Int){Page(page:$page,perPage:50){pageInfo{hasNextPage} airingSchedules(airingAt_greater:$from,airingAt_lesser:$to,sort:TIME_DESC){
airingAt episode media{id title{romaji english} format trending popularity averageScore isAdult countryOfOrigin
relations{edges{relationType node{id type countryOfOrigin}}}}}}}"""


def anilist(variables: dict) -> list[dict]:
    """Every airing in the window: AniList gives 50 a page, so read page after page."""
    rows = []
    for page in range(1, PAGES + 1):
        req = urllib.request.Request("https://graphql.anilist.co", json.dumps({"query": QUERY, "variables": {**variables, "page": page}}).encode(),
                                     {"Content-Type": "application/json", "User-Agent": "clip-daily"})
        got = json.loads(urllib.request.urlopen(req, timeout=60).read())["data"]["Page"]
        rows += got["airingSchedules"]
        if not got["pageInfo"]["hasNextPage"]:
            break
        time.sleep(1)
    return rows


def manhwa_id(media: dict) -> int | None:
    """The AniList id of the Korean or Chinese comic or novel this anime is adapted from (Manhwa Index lists that one), else None."""
    for e in media["relations"]["edges"]:
        if e["relationType"] == "ADAPTATION" and e["node"].get("countryOfOrigin") in KOREA_CHINA:
            return e["node"]["id"]
    return None


def state_file(page: str | None = None) -> Path:
    return STATE if not page else STATE.with_name(f"clips-state-{page}.json")


def state(page: str | None = None) -> dict:
    f = state_file(page)
    return json.loads(f.read_text()) if f.exists() else {"done": [], "failed": {}, "days": {}, "shows": []}


def save(st: dict, page: str | None = None) -> None:
    STATE.parent.mkdir(exist_ok=True)
    state_file(page).write_text(json.dumps(st, indent=1))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def tier_of(c: dict, now: int) -> int | None:
    """0 = aired in the last 3 days (news, first). 1 = 3-10 days ago and talked about. 2 = 10-15 days ago and well scored. Else None."""
    age = (now - c["aired"]) / 3600
    if age <= FRESH_HOURS:
        return 0
    if age <= RECENT_HOURS:
        return 1 if (c["trending"] >= 10 or c["score"] >= GOOD_SCORE) else None
    return 2 if c["score"] >= GOOD_SCORE and c["popularity"] >= 10000 else None


def to_candidate(r: dict) -> dict | None:
    """One AniList airing as a candidate, or None for adult shows and formats we do not clip."""
    m = r["media"]
    if m["isAdult"] or m["format"] not in FORMATS:
        return None
    return {"key": f"{m['id']}-{r['episode']}", "anilist_id": m["id"], "episode": r["episode"], "aired": r["airingAt"],
            "title": m["title"]["english"] or m["title"]["romaji"], "romaji": m["title"]["romaji"],
            "trending": m["trending"] or 0, "popularity": m["popularity"] or 0, "score": m["averageScore"] or 0, "manga_id": manhwa_id(m)}


def airing(now: int, hours: int) -> list[dict]:
    """Every clippable episode that aired between MIN_AGE_HOURS and <hours> ago, shuffled."""
    rows = anilist({"from": now - hours * 3600, "to": now - MIN_AGE_HOURS * 3600})
    out = [c for c in map(to_candidate, rows) if c]
    random.shuffle(out)
    return out


def candidates(now: int) -> list[dict]:
    out = []
    for c in airing(now, WINDOW_HOURS):
        c["tier"] = tier_of(c, now)
        if c["tier"] is not None:
            out.append(c)
    # fresh first; inside a tier the most talked about; the oldest tier is shuffled so the backlog is a random good pick
    return sorted(out, key=lambda c: (c["tier"], 0 if c["tier"] == 2 else -c["trending"], -c["popularity"]))


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def made_today(st: dict, page: str) -> int:
    return st["days"].get(today(), {}).get(page, 0)


def pick() -> None:
    """One job per run, with the Pages it is for. No daily limit: whatever is left is clipped, freshest and most
    trending first. A show that was clipped lately waits behind other shows. Both Pages get every pick."""
    st = state()
    recent_shows = {k.split("-")[0] for k in st["done"][-12:]}
    usable = [c for c in candidates(int(time.time())) if c["key"] not in st["done"] and st["failed"].get(c["key"], 0) < MAX_TRIES]
    usable.sort(key=lambda c: (c["tier"], str(c["anilist_id"]) in recent_shows))
    chosen = usable[0] if usable else None
    if not chosen:
        print("nothing to clip now", file=sys.stderr)
        return
    # Both Pages are separate: each gets its own branded copy of every clip, of the same episode, on its own quota.
    pages = [p for p in ("whereanime", "manhwaindex") if made_today(st, p) < PER_DAY]
    if not pages:
        print("both Pages are at their daily number", file=sys.stderr)
        return
    job = ROOT / "jobs" / f"clip-{slug(chosen['title'])}-e{chosen['episode']:02d}"
    job.mkdir(parents=True, exist_ok=True)
    (job / "source.json").write_text(json.dumps({"type": "anime", "title": chosen["title"], "episode": chosen["episode"], "lang": "sub"}, indent=1))
    (job / "clip.json").write_text(json.dumps({"kind": "clip", "pages": pages, "title": chosen["title"], "episode": chosen["episode"],
                                               "anilist_id": chosen["anilist_id"], "manga_id": chosen["manga_id"], "key": chosen["key"]}, indent=1))
    print(f"picked {chosen['title']} episode {chosen['episode']} (tier {chosen['tier']}, trending {chosen['trending']}) for {', '.join(pages)}", file=sys.stderr)
    print(f"job=jobs/{job.name}")


def mark(job: str, result: str) -> None:
    meta = json.loads((ROOT / job / "clip.json").read_text())
    own = meta["pages"][0] if meta["pages"][0] in clip_pages.PAGES else None   # the new Pages keep their own state file
    st, key = state(own), meta["key"]
    if result == "done":
        st["done"] = (st["done"] + [key])[-500:]
        day = st["days"].get(today(), {})
        for page in meta["pages"]:
            day[page] = day.get(page, 0) + 1
            st["shows"] = (st.get("shows", []) + [{"page": page, "show": meta.get("show") or meta["anilist_id"], "at": int(time.time())}])[-300:]
        st["days"] = {today(): day}
    else:
        st["failed"][key] = st["failed"].get(key, 0) + 1
    save(st, own)
    print(f"{key}: {result}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["pick"] and len(sys.argv) == 3:
        clip_pages.pick_page(sys.argv[2])
    elif sys.argv[1:2] == ["pick"]:
        pick()
    elif sys.argv[1:2] == ["mark"] and len(sys.argv) == 4:
        mark(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
