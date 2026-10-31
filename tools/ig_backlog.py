"""Copy videos a Facebook Page already posted to its linked Instagram account, oldest first, with a gap.

Usage: python ig_backlog.py <page> <gap_minutes> <fb_video_id> [<fb_video_id> ...]
Instagram fetches each file straight from Facebook's own video link, so nothing is downloaded here.
The caption is the Facebook post's text.
"""
from __future__ import annotations

import os
import sys
import time

import requests

import post_ig
from post_fb import env_key


def main() -> None:
    page, gap, ids = sys.argv[1].upper(), float(sys.argv[2]), sys.argv[3:]
    page_id, token = env_key(f"{page}_FB_PAGE_ID"), env_key(f"{page}_FB_PAGE_TOKEN")
    ig_id, name = post_ig.ig_account(page_id, token)
    for n, vid in enumerate(ids):
        if n:
            print(f"waiting {gap:.0f} min", flush=True)
            time.sleep(gap * 60)
        info = requests.get(f"{post_ig.GRAPH}/{vid}", params={"fields": "source,description",
                                                              "access_token": token}, timeout=60).json()
        if "source" not in info:
            print(f"::warning::{vid}: no video link: {info.get('error', {}).get('message', info)}")
            continue
        os.environ["VIDEO_URL"] = info["source"]
        try:
            media = post_ig.upload_reel(ig_id, token, None, info.get("description", ""))
        except SystemExit as exc:
            print(f"::warning::{vid}: {exc}")
            continue
        link = requests.get(f"{post_ig.GRAPH}/{media}", params={"fields": "permalink", "access_token": token},
                            timeout=60).json().get("permalink", media)
        print(f"@{name}: {vid} -> {link}", flush=True)


if __name__ == "__main__":
    main()
