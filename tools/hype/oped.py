"""Opening, ending, recap and preview zones to keep out of the ranking.

1. AniSkip (community timestamps, found through the AniList MAL id).
2. Fallback: fixed zones at both ends, marked uncertain.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

FALLBACK_START = 150.0   # seconds kept out at the start when nobody has timestamps
FALLBACK_END = 200.0     # seconds kept out at the end (ending song and preview)


def _get(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "hype-detect", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def mal_id(title: str) -> int | None:
    query = "query($s:String){Media(search:$s,type:ANIME){idMal}}"
    body = json.dumps({"query": query, "variables": {"s": title}}).encode()
    try:
        return _get("https://graphql.anilist.co", body, {"Content-Type": "application/json"})["data"]["Media"]["idMal"]
    except Exception:
        return None


def aniskip(mal: int, episode: int, length: float) -> list[tuple[float, float]]:
    types = "&".join(f"types={t}" for t in ("op", "ed", "mixed-op", "mixed-ed", "recap"))
    url = f"https://api.aniskip.com/v2/skip-times/{mal}/{episode}?{types}&episodeLength={int(length)}"
    try:
        found = _get(url).get("results", [])
    except Exception:
        return []
    return [(r["interval"]["startTime"], r["interval"]["endTime"]) for r in found]


def exclusions(title: str | None, episode: int | None, length: float) -> dict:
    """{'zones': [(a, b)], 'source': 'aniskip'|'fallback', 'uncertain': bool}"""
    if title and episode:
        mal = mal_id(title)
        zones = aniskip(mal, episode, length) if mal else []
        if zones:
            return {"zones": zones, "source": "aniskip", "uncertain": False}
    return {"zones": [(0.0, FALLBACK_START), (max(0.0, length - FALLBACK_END), length)], "source": "fallback", "uncertain": True}
