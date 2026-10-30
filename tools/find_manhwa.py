"""Find one manhwa on every source we can download from, under all of its names.

Usage: python find_manhwa.py "<any title>"

AniList gives the other names (official English, Korean romaji, fan titles). Each name is searched on
Webtoon, WeebCentral, FanFox, MangaHere and MangaDex. The output is a ready "sources" list for a
job's source.json ({"type": "manga", "sources": [...], "chapters": "1-6"}), best source first.
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import quote

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130"}
# official and clean sources first: Webtoon has no scanlator marks, MangaDex groups vary
ORDER = ["webtoon", "weebcentral", "fanfox", "mangahere", "mangadex"]


def get(url: str, **kw) -> requests.Response:
    return requests.get(url, headers=UA, timeout=30, **kw)


def names(title: str) -> list[str]:
    """The title plus every English-letter name AniList knows for it."""
    q = ("query($s:String){Media(search:$s,type:MANGA){title{romaji english} synonyms popularity countryOfOrigin}}")
    try:
        m = requests.post("https://graphql.anilist.co", json={"query": q, "variables": {"s": title}},
                          timeout=30).json()["data"]["Media"]
    except Exception as exc:  # AniList down: search the given name only
        print(f"AniList: {exc}", file=sys.stderr)
        return [title]
    found = [title, m["title"]["english"], m["title"]["romaji"], *m["synonyms"]]
    out: list[str] = []
    for n in found:
        n = (n or "").strip()
        if n and n.isascii() and n.lower() not in {o.lower() for o in out}:
            out.append(n)
    print(f"AniList: popularity {m['popularity']}, {m['countryOfOrigin']}, names: {out}", file=sys.stderr)
    return out


def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower().replace("'s", "s"))


def matches(name: str, slug: str) -> bool:
    return key(name) == key(slug) or (len(key(name)) > 12 and key(name) in key(slug))


def webtoon(name: str) -> list[str]:
    t = get(f"https://www.webtoons.com/en/search?keyword={quote(name)}").text
    urls = set(re.findall(r"https://www\.webtoons\.com/en/(?!canvas)[a-z-]+/([a-z0-9-]+)/list\?title_no=(\d+)", t))
    return [f"https://www.webtoons.com/en/x/{s}/list?title_no={n}" for s, n in urls if matches(name, s)]


def weebcentral(name: str) -> list[str]:
    t = get("https://weebcentral.com/search/data", params={
        # its search fails on whole titles, so ask for the longest word and match the title here
        "text": max(re.sub(r"'s\b|[^\w ]", "", name).split(), key=len), "sort": "Best Match", "order": "Descending", "official": "Any", "anime": "Any",
        "adult": "Any", "display_mode": "Full Display"}).text
    return [f"https://weebcentral.com/series/{i}/{s}" for i, s in set(re.findall(r"series/([A-Z0-9]{20,})/([A-Za-z0-9-]+)", t))
            if matches(name, s)]


def mangafox(host: str, name: str) -> list[str]:
    t = get(f"https://{host}/search", params={"title": name}).text
    return [f"https://{host}/manga/{s}/" for s in set(re.findall(r'/manga/([a-z0-9_]+)/"', t)) if matches(name, s)]


def mangadex(name: str) -> list[str]:
    out = []
    for m in get("https://api.mangadex.org/manga", params={"title": name, "limit": 5}).json().get("data", []):
        titles = [*m["attributes"]["title"].values(), *(v for a in m["attributes"]["altTitles"] for v in a.values())]
        if not any(matches(name, t) for t in titles):
            continue
        agg = get(f"https://api.mangadex.org/manga/{m['id']}/aggregate", params={"translatedLanguage[]": "en"}).json()
        count = sum(len(v["chapters"]) for v in (agg.get("volumes") or {}).values())
        if count >= 10:  # a few loose chapters is not a source
            out.append(f"https://mangadex.org/title/{m['id']}")
    return out


SEARCH = {"webtoon": webtoon, "weebcentral": weebcentral, "fanfox": lambda n: mangafox("fanfox.net", n),
          "mangahere": lambda n: mangafox("www.mangahere.cc", n), "mangadex": mangadex}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    found: dict[str, list[str]] = {s: [] for s in ORDER}
    for name in names(sys.argv[1]):
        for site in ORDER:
            try:
                for url in SEARCH[site](name):
                    if url not in found[site]:
                        found[site].append(url)
            except Exception as exc:
                print(f"{site} '{name}': {str(exc)[:100]}", file=sys.stderr)
    for site in ORDER:
        print(f"{site:12} {', '.join(found[site]) or '-'}", file=sys.stderr)
    print(json.dumps([u for s in ORDER for u in found[s][:1]], indent=1))


if __name__ == "__main__":
    main()
