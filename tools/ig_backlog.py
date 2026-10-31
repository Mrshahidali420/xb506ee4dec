"""Copy videos a Facebook Page already posted to its linked Instagram account, oldest first, with a gap.

Usage: python ig_backlog.py <page> <gap_minutes> <fb_video_id>@<archive tag>/<file> ...
The file comes from the private recap-assets archive (ARCHIVE_TOKEN), is staged on a public release of this
repo (GH_TOKEN) so Instagram can fetch it, and is removed again. The caption is the Facebook post's text.
Instagram cannot fetch Facebook's own video links (error 2207076), so the archive copy is used.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

import post_ig
from post_fb import env_key

REPO = os.environ.get("GITHUB_REPOSITORY", "Mrshahidali420/xb506ee4dec")
ASSETS = REPO.split("/")[0] + "/recap-assets"


def gh(*args: str, token: str) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, env={**os.environ, "GH_TOKEN": token})
    if out.returncode:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {out.stderr.strip()[-300:]}")
    return out.stdout


def stage(tag: str, name: str) -> tuple[Path, str]:
    """Archive file -> public release -> direct link Instagram can fetch."""
    local = Path(f"/tmp/ig-{int(time.time())}-{name}")
    gh("release", "download", tag, "-R", ASSETS, "-p", name, "-O", str(local), token=os.environ["ARCHIVE_TOKEN"])
    gh("release", "upload", "ig-staging", str(local), "-R", REPO, "--clobber", token=os.environ["GH_TOKEN"])
    link = f"https://github.com/{REPO}/releases/download/ig-staging/{local.name}"
    return local, requests.head(link, allow_redirects=True, timeout=60).url


def main() -> None:
    page, gap, items = sys.argv[1].upper(), float(sys.argv[2]), sys.argv[3:]
    page_id, token = env_key(f"{page}_FB_PAGE_ID"), env_key(f"{page}_FB_PAGE_TOKEN")
    ig_id, name = post_ig.ig_account(page_id, token)
    for n, item in enumerate(items):
        if n:
            print(f"waiting {gap:.0f} min", flush=True)
            time.sleep(gap * 60)
        vid, path = item.split("@", 1)
        tag, file = path.split("/", 1)
        caption = requests.get(f"{post_ig.GRAPH}/{vid}", params={"fields": "description", "access_token": token},
                               timeout=60).json().get("description", "")
        local = None
        try:
            local, os.environ["VIDEO_URL"] = stage(tag, file)
            media = post_ig.upload_reel(ig_id, token, local, caption)
            link = requests.get(f"{post_ig.GRAPH}/{media}", params={"fields": "permalink", "access_token": token},
                                timeout=60).json().get("permalink", media)
            print(f"@{name}: {vid} -> {link}", flush=True)
        except (SystemExit, RuntimeError, requests.RequestException) as exc:
            print(f"::warning::{vid}: {exc}", flush=True)
        finally:
            if local:
                subprocess.run(["gh", "release", "delete-asset", "ig-staging", local.name, "-R", REPO, "-y"],
                               capture_output=True)


if __name__ == "__main__":
    main()
