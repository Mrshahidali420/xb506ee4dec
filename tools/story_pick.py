"""Pick whole manhwa stories for the weekly long video (Shahid, 9 Nov 2026: complete stories, not parts).

  python story_pick.py discover [n]     finished Korean manhwa of 30-120 chapters, most popular first (AniList, no key)
  python story_pick.py add "<title>"    look the title up (status, chapters), find its sources, put it in daily/stories.json
  python story_pick.py show             the shelf: each story, its status, chapters covered, and the next block
  python story_pick.py next             the next block to build, as JSON (daily.py uses story_job instead)

The block rule:
  finished              one video, the whole story (chapters 1..total)
  ongoing, 40+ left     the next BLOCK chapters in bulk (first 30, then the next 30...)
  ongoing, under 40     everything left up to the latest chapter: the current arc in full
A story is marked "done" once its last block is queued, so it is never picked twice.
daily/stories.json (git-ignored, like plan.json): {"stories": [{"title", "slug", "status", "total", "done",
"sources", "blocks": [{"job", "chapters"}]}]}
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()
ROOT = Path(__file__).resolve().parent.parent
SHELF = ROOT / "daily" / "stories.json"
BLOCK = 30        # chapters per bulk block of an ongoing story
BULK_FROM = 40    # an ongoing story with at least this many chapters left is covered in bulk blocks
MIN_FULL, MAX_FULL = 30, 120  # a finished story fits one video in this chapter range
ANILIST = "https://graphql.anilist.co"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def block_for(status: str, total: int, done: int) -> tuple[int, int] | None:
    """The next chapter range (first, last) to recap, or None when the story is finished covering."""
    left = total - done
    if left <= 0:
        return None
    if status == "FINISHED":
        return done + 1, total
    if left >= BULK_FROM:
        return done + 1, done + BLOCK
    return done + 1, total


def anilist(query: str, variables: dict) -> dict:
    r = requests.post(ANILIST, json={"query": query, "variables": variables}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def discover(n: int) -> None:
    query = """query($p:Int){Page(page:$p,perPage:50){media(type:MANGA,countryOfOrigin:"KR",status:FINISHED,
      sort:POPULARITY_DESC,format:MANGA){title{english romaji} chapters popularity averageScore genres}}}"""
    shelf = load()
    have = {s["slug"] for s in shelf["stories"]}
    shown = 0
    for page in (1, 2):
        for m in anilist(query, {"p": page})["Page"]["media"]:
            name = m["title"]["english"] or m["title"]["romaji"]
            if m["chapters"] and MIN_FULL <= m["chapters"] <= MAX_FULL and slug(name) not in have:
                print(f"{name} | {m['chapters']} ch | pop {m['popularity']} | score {m['averageScore']} | {', '.join(m['genres'][:3])}")
                shown += 1
                if shown >= n:
                    return


def load() -> dict:
    return json.loads(SHELF.read_text(encoding="utf-8")) if SHELF.exists() else {"stories": []}


def save(shelf: dict) -> None:
    SHELF.write_text(json.dumps(shelf, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def add(title: str, total: int | None = None) -> None:
    query = """query($s:String){Media(search:$s,type:MANGA,countryOfOrigin:"KR"){title{english romaji} status chapters}}"""
    m = anilist(query, {"s": title})["Media"]
    if not m:
        sys.exit(f"AniList knows no Korean manhwa called {title!r}")
    name = m["title"]["english"] or m["title"]["romaji"]
    total = total or m["chapters"]
    if not total:  # ongoing titles often carry no chapter count on AniList: pass it by hand (`add "<title>" <chapters>`)
        sys.exit(f"{name}: AniList has no chapter count (status {m['status']}); run: story_pick.py add \"{title}\" <chapters>")
    found = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "find_manhwa.py"), title], capture_output=True, text=True)
    if found.returncode:
        sys.exit(f"{name}: find_manhwa failed: {found.stderr.strip()[-200:]}")
    out = found.stdout
    sources = json.loads(out) if out.strip().startswith("[") else []  # find_manhwa prints the list alone on stdout
    if not sources:
        sys.exit(f"{name}: no readable source found")
    shelf = load()
    if any(s["slug"] == slug(name) for s in shelf["stories"]):
        sys.exit(f"{name} is on the shelf already")
    shelf["stories"].append({"title": name, "slug": slug(name), "status": m["status"], "total": total, "done": 0,
                             "sources": sources, "blocks": []})
    save(shelf)
    print(f"added {name}: {m['status']}, {total} chapters, {len(sources)} sources, first block {block_for(m['status'], total, 0)}")


def next_story(shelf: dict) -> tuple[dict, tuple[int, int]] | None:
    for s in shelf["stories"]:
        block = block_for(s["status"], s["total"], s["done"])
        if block:
            return s, block
    return None


def story_job() -> tuple[str, dict] | None:
    """For daily.py: the job folder and source.json of the next story block. Nothing is saved here: a prep that
    fails must not use the block up. daily.build() calls take_block() once the real build has started."""
    found = next_story(load())
    if not found:
        return None
    s, (a, b) = found
    job = f"jobs/manhwa-{s['slug']}-s{len(s['blocks']) + 1}"
    return job, {"type": "manga", "chapters": f"{a}-{b}", "sources": s["sources"], "story": s["title"], "story_status": s["status"]}


def take_block(job: str, story: str, chapters: str) -> None:
    """Mark a block as covered (its build has started). Safe to call twice for the same job."""
    shelf = load()
    s = next((x for x in shelf["stories"] if x["title"] == story), None)
    if s is None or any(b["job"] == job for b in s["blocks"]):
        return
    s["blocks"].append({"job": job, "chapters": chapters})
    s["done"] = int(chapters.split("-")[1])
    save(shelf)


def show() -> None:
    for s in load()["stories"]:
        nxt = block_for(s["status"], s["total"], s["done"])
        print(f"{s['title']} | {s['status']} | {s['done']}/{s['total']} | blocks {len(s['blocks'])} | next {nxt or 'finished'}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "discover":
        discover(int(sys.argv[2]) if len(sys.argv) > 2 else 15)
    elif cmd == "add" and len(sys.argv) > 2:
        add(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif cmd == "show":
        show()
    elif cmd == "next":
        found = next_story(load())
        print(json.dumps({"story": found[0]["title"], "chapters": found[1]} if found else None))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
