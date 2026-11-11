"""Read-only check: can each Page token read insights, and how did the last posts do? python page_insights.py"""
from __future__ import annotations

import os

import requests

GRAPH = "https://graph.facebook.com/v21.0"
PAGES = {"animerewind": "ANIME", "manhwax": "MANHWAX", "spoiler": "SPOILER"}


def get(path: str, token: str, **params) -> dict:
    r = requests.get(f"{GRAPH}/{path}", params={**params, "access_token": token}, timeout=30)
    return r.json()


def main() -> None:
    for page, prefix in PAGES.items():
        pid, token = os.environ.get(f"{prefix}_FB_PAGE_ID", ""), os.environ.get(f"{prefix}_FB_PAGE_TOKEN", "")
        if not (pid and token):
            print(f"{page}: secret missing")
            continue
        day = get(f"{pid}/insights", token, metric="page_media_view", period="days_28")
        print(f"{page}: page insights ->", day.get("error", {}).get("message") or "OK",
              [v.get("value") for d in day.get("data", []) for v in d.get("values", [])][-3:])
        reels = get(f"{pid}/video_reels", token, fields="id,description,created_time", limit=5)
        for v in reels.get("data", []):
            ins = get(f"{v['id']}/video_insights", token)
            views = {d["name"]: (d["values"][0]["value"] if d.get("values") else None) for d in ins.get("data", [])
                     if d["name"] in ("post_video_views", "blue_reels_play_count", "post_impressions_unique")}
            print(f"  {v['created_time'][:16]} {v['id']} {views or ins.get('error', {}).get('message', 'no data')}")


if __name__ == "__main__":
    main()
