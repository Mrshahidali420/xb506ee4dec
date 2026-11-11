"""Post a short video as a Facebook Page Story and an Instagram Story. Used by post_clips.py after a clip reel.

Usage: python post_story.py <page> <video.mp4> [--go]     (reads <PAGE>_FB_PAGE_ID / <PAGE>_FB_PAGE_TOKEN like post_fb.py)

The Graph API cannot put a link sticker on a story, so a story carries no link. Each part is separate:
a failed Facebook story does not stop the Instagram one.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import post_fb
import post_ig

requests = post_fb.requests
GRAPH = post_fb.GRAPH
RUPLOAD = "https://rupload.facebook.com/"


def fb_story(page_id: str, token: str, video: Path) -> str:
    """Page Story: start, upload the bytes to Facebook's upload host, finish. Returns the story's post id."""
    url = f"{GRAPH}/{page_id}/video_stories"
    start = requests.post(url, data={"upload_phase": "start", "access_token": token}, timeout=60).json()
    if "video_id" not in start:
        raise RuntimeError(f"story start failed: {start.get('error', {}).get('message', start)}")
    vid = start["video_id"]
    target = start.get("upload_url") if str(start.get("upload_url", "")).startswith(RUPLOAD) else f"{RUPLOAD}video-upload/v21.0/{vid}"
    data = video.read_bytes()
    sent = requests.post(target, data=data, timeout=600, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}).json()
    if not sent.get("success"):
        raise RuntimeError(f"story upload failed: {sent.get('error', sent)}")
    done = requests.post(url, data={"upload_phase": "finish", "video_id": vid, "access_token": token}, timeout=120).json()
    if not done.get("success", "post_id" in done):
        raise RuntimeError(f"story finish failed: {done.get('error', {}).get('message', done)}")
    return str(done.get("post_id") or vid)


def ig_story(ig_id: str, token: str, video: Path) -> str:
    """Instagram Story by resumable upload. Returns the media id."""
    made = requests.post(f"{GRAPH}/{ig_id}/media", data={"media_type": "STORIES", "upload_type": "resumable", "access_token": token}, timeout=60).json()
    if "id" not in made:
        raise RuntimeError(f"story container failed: {made.get('error', {}).get('message', made)}")
    post_ig.send_bytes(made, token, video)
    for _ in range(60):
        state = requests.get(f"{GRAPH}/{made['id']}", params={"fields": "status_code,status", "access_token": token}, timeout=60).json()
        if state.get("status_code") == "FINISHED":
            break
        if state.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram could not process the story: {state.get('status')}")
        time.sleep(10)
    else:
        raise RuntimeError("Instagram took over 10 minutes to process the story")
    done = requests.post(f"{GRAPH}/{ig_id}/media_publish", data={"creation_id": made["id"], "access_token": token}, timeout=60).json()
    if "id" not in done:
        raise RuntimeError(f"story publish failed: {done.get('error', {}).get('message', done)}")
    return done["id"]


def both(page_id: str, token: str, video: Path) -> dict:
    """Both stories; each part reports its id or its error. Never raises."""
    out: dict = {"at": time.time()}
    try:
        out["fb"] = fb_story(page_id, token, video)
    except Exception as exc:  # noqa: BLE001 - a story must never stop the post
        out["fb_error"] = str(exc)[:200]
    try:
        ig_id, _ = post_ig.ig_account(page_id, token)
        out["ig"] = ig_story(ig_id, token, video)
    except BaseException as exc:  # noqa: BLE001 - post_ig exits with sys.exit on some errors
        out["ig_error"] = str(exc)[:200]
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--go"]
    if len(args) != 2:
        sys.exit(__doc__)
    page, video = args[0].upper(), Path(args[1])
    page_id, token = post_fb.env_key(f"{page}_FB_PAGE_ID"), post_fb.env_key(f"{page}_FB_PAGE_TOKEN")
    if not (page_id and token and video.exists()):
        sys.exit("missing page id, token or video")
    if "--go" not in sys.argv:
        print("dry run: nothing posted (add --go)")
        return
    print(both(page_id, token, video))


if __name__ == "__main__":
    main()
