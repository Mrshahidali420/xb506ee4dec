"""Choose today's trending episode to clip, for the two Pages. No AI, no browser.

  python tools/clip_daily.py pick     makes jobs/clip-<slug>-e<NN>/ (source.json, clip.json) and prints job=<folder> for GitHub
  python tools/clip_daily.py mark <job> done|fail    records the result in clips/state.json

How it picks: AniList lists every episode that aired in the last 48 hours. The one whose show is trending
the most (then the most popular) wins, unless it was clipped already or failed twice. WhereAnime takes any
show. Manhwa Index takes only a show adapted from a manhwa or manhua (its source work comes from Korea or China;
the post links that comic's page), so the same episode may go to both Pages: one job, two logo copies.
Nothing here posts. clips/state.json is the memory, committed by the workflow.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "clips" / "state.json"
WINDOW_HOURS = 48
MIN_AGE_HOURS = 3          # a fresh release needs time to reach the stream site
MAX_TRIES = 2
PER_DAY = int(os.environ.get("CLIPS_PER_DAY", "2"))
FORMATS = {"TV", "TV_SHORT", "ONA"}
KOREA_CHINA = {"KR", "CN"}
QUERY = """query($from:Int,$to:Int){Page(perPage:50){airingSchedules(airingAt_greater:$from,airingAt_lesser:$to,sort:TIME_DESC){
airingAt episode media{id title{romaji english} format trending popularity isAdult countryOfOrigin
relations{edges{relationType node{id type countryOfOrigin}}}}}}}"""


def anilist(variables: dict) -> list[dict]:
    req = urllib.request.Request("https://graphql.anilist.co", json.dumps({"query": QUERY, "variables": variables}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "clip-daily"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["data"]["Page"]["airingSchedules"]


def manhwa_id(media: dict) -> int | None:
    """The AniList id of the Korean or Chinese comic or novel this anime is adapted from (Manhwa Index lists that one), else None."""
    for e in media["relations"]["edges"]:
        if e["relationType"] == "ADAPTATION" and e["node"].get("countryOfOrigin") in KOREA_CHINA:
            return e["node"]["id"]
    return None


def state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"done": [], "failed": {}, "days": {}}


def save(st: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def candidates(now: int) -> list[dict]:
    rows = anilist({"from": now - WINDOW_HOURS * 3600, "to": now - MIN_AGE_HOURS * 3600})
    out = []
    for r in rows:
        m = r["media"]
        if m["isAdult"] or m["format"] not in FORMATS:
            continue
        out.append({"key": f"{m['id']}-{r['episode']}", "anilist_id": m["id"], "episode": r["episode"], "aired": r["airingAt"],
                    "title": m["title"]["english"] or m["title"]["romaji"], "romaji": m["title"]["romaji"],
                    "trending": m["trending"] or 0, "popularity": m["popularity"] or 0, "manga_id": manhwa_id(m)})
    return sorted(out, key=lambda c: (c["trending"], c["popularity"]), reverse=True)


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def made_today(st: dict, page: str) -> int:
    return st["days"].get(today(), {}).get(page, 0)


def pick() -> None:
    """WhereAnime: the most trending episode. Manhwa Index: the most trending anime made from a manhwa or manhua,
    even when it is not first overall (its own cap of 1 a day). One job per run, with the Pages it is for."""
    st = state()
    usable = [c for c in candidates(int(time.time())) if c["key"] not in st["done"] and st["failed"].get(c["key"], 0) < MAX_TRIES]
    best = usable[0] if usable and made_today(st, "whereanime") < PER_DAY else None
    manhwa = next((c for c in usable if c["manga_id"]), None) if made_today(st, "manhwaindex") < 1 else None
    if best and best["manga_id"] and manhwa is None:
        manhwa = best
    chosen = best or manhwa
    if not chosen:
        print("nothing to clip now", file=sys.stderr)
        return
    pages = (["whereanime"] if chosen is best else []) + (["manhwaindex"] if manhwa and (chosen is manhwa or best is manhwa) else [])
    job = ROOT / "jobs" / f"clip-{slug(chosen['title'])}-e{chosen['episode']:02d}"
    job.mkdir(parents=True, exist_ok=True)
    (job / "source.json").write_text(json.dumps({"type": "anime", "title": chosen["title"], "episode": chosen["episode"], "lang": "sub"}, indent=1))
    (job / "clip.json").write_text(json.dumps({"kind": "clip", "pages": pages, "title": chosen["title"], "episode": chosen["episode"],
                                               "anilist_id": chosen["anilist_id"], "manga_id": chosen["manga_id"], "key": chosen["key"]}, indent=1))
    print(f"picked {chosen['title']} episode {chosen['episode']} (trending {chosen['trending']}) for {', '.join(pages)}", file=sys.stderr)
    print(f"job=jobs/{job.name}")


def mark(job: str, result: str) -> None:
    meta = json.loads((ROOT / job / "clip.json").read_text())
    st, key = state(), meta["key"]
    if result == "done":
        st["done"] = (st["done"] + [key])[-500:]
        day = st["days"].get(today(), {})
        for page in meta["pages"]:
            day[page] = day.get(page, 0) + 1
        st["days"] = {today(): day}
    else:
        st["failed"][key] = st["failed"].get(key, 0) + 1
    save(st)
    print(f"{key}: {result}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["pick"]:
        pick()
    elif sys.argv[1:2] == ["mark"] and len(sys.argv) == 4:
        mark(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
