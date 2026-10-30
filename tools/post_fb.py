"""Post a finished recap video to one of our Facebook Pages.

Usage: python post_fb.py <page> <video.mp4> "<caption>" [--go]
  <page>   short name; reads <PAGE>_FB_PAGE_ID and <PAGE>_FB_PAGE_TOKEN
           from the environment or ~/.config/watch/.env (e.g. spoiler)
  --go     really post. Without it, only checks the token and the file (dry run).

Long videos go through the Page video endpoint with a resumable upload,
because the Reels API stops at 90 seconds.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

GRAPH = "https://graph.facebook.com/v21.0"
VIDEO_GRAPH = "https://graph-video.facebook.com/v21.0"


def env_key(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env = Path.home() / ".config/watch/.env"
    lines = env.read_text(encoding="utf-8-sig").splitlines() if env.exists() else []
    return next((l.split("=", 1)[1].strip().strip('"') for l in lines if l.startswith(name + "=")), "")


def check(resp: requests.Response, step: str) -> dict:
    data = resp.json()
    if resp.status_code != 200 or "error" in data:
        msg = data.get("error", {}).get("message", f"HTTP {resp.status_code}")
        sys.exit(f"{step} failed: {msg}")
    return data


def upload(page_id: str, token: str, video: Path, caption: str) -> str:
    size = video.stat().st_size
    url = f"{VIDEO_GRAPH}/{page_id}/videos"
    start = check(requests.post(url, data={"upload_phase": "start", "file_size": size, "access_token": token},
                                timeout=60), "upload start")
    session, offset, end = start["upload_session_id"], int(start["start_offset"]), int(start["end_offset"])
    with video.open("rb") as fh:
        while offset < end:
            fh.seek(offset)
            chunk = fh.read(end - offset)
            got = check(requests.post(url, data={"upload_phase": "transfer", "upload_session_id": session,
                                                 "start_offset": offset, "access_token": token},
                                      files={"video_file_chunk": ("chunk", chunk)}, timeout=600), "upload chunk")
            offset, end = int(got["start_offset"]), int(got["end_offset"])
            print(f"  uploaded {offset * 100 // size}%", flush=True)
    check(requests.post(url, data={"upload_phase": "finish", "upload_session_id": session, "description": caption,
                                   "published": "true", "access_token": token}, timeout=120), "upload finish")
    return start["video_id"]


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--go"]
    if len(args) != 3:
        sys.exit(__doc__)
    page, video, caption = args[0].upper(), Path(args[1]), args[2]
    page_id, token = env_key(f"{page}_FB_PAGE_ID"), env_key(f"{page}_FB_PAGE_TOKEN")
    if not page_id or not token:
        sys.exit(f"missing {page}_FB_PAGE_ID or {page}_FB_PAGE_TOKEN")
    if not video.exists():
        sys.exit(f"no video at {video}")
    me = check(requests.get(f"{GRAPH}/me", params={"fields": "id,name", "access_token": token}, timeout=30), "token check")
    if me["id"] != page_id:
        sys.exit("the token belongs to a different Page")
    print(f"page: {me['name']} | video: {video.name} ({video.stat().st_size // 2**20} MB)\ncaption: {caption}")
    if "--go" not in sys.argv:
        print("dry run: nothing posted (add --go to post)")
        return
    video_id = upload(page_id, token, video, caption)
    print(f"posted: https://www.facebook.com/{video_id}")


if __name__ == "__main__":
    main()
