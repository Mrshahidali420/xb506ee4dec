"""Post a video as an Instagram Reel through the Facebook Page it is linked to (Graph API, resumable upload).

Usage: python post_ig.py <page> <video.mp4> "<caption>" ["<comment>"] [--go]
  <page>   short name; reads <PAGE>_FB_PAGE_ID and <PAGE>_FB_PAGE_TOKEN (the token needs
           instagram_basic + instagram_content_publish). Without --go it only checks the account.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

from post_fb import env_key

GRAPH = "https://graph.facebook.com/v21.0"


def ig_account(page_id: str, token: str) -> tuple[str, str]:
    r = requests.get(f"{GRAPH}/{page_id}", params={"fields": "instagram_business_account{id,username}",
                                                   "access_token": token}, timeout=60).json()
    acct = r.get("instagram_business_account")
    if not acct:
        sys.exit(f"no Instagram account linked to this page: {r.get('error', {}).get('message', r)}")
    return acct["id"], acct.get("username", "?")


def upload_reel(ig_id: str, token: str, video: Path, caption: str) -> str:
    """Create a Reel container, send the bytes, wait for Instagram to process it, publish. Returns the media id."""
    made = requests.post(f"{GRAPH}/{ig_id}/media", data={"media_type": "REELS", "upload_type": "resumable",
                                                         "caption": caption, "share_to_feed": "true",
                                                         "access_token": token}, timeout=60).json()
    if "id" not in made:
        sys.exit(f"container failed: {made}")
    data = video.read_bytes()
    sent = requests.post(made["uri"], data=data, timeout=600,
                         headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data))}).json()
    if not sent.get("success"):
        sys.exit(f"upload failed: {sent}")
    for _ in range(60):  # Instagram transcodes the video before it can go live
        state = requests.get(f"{GRAPH}/{made['id']}", params={"fields": "status_code,status",
                                                              "access_token": token}, timeout=60).json()
        print("  processing:", state.get("status_code"), flush=True)
        if state.get("status_code") == "FINISHED":
            break
        if state.get("status_code") == "ERROR":
            sys.exit(f"Instagram could not process the video: {state.get('status')}")
        time.sleep(10)
    else:
        sys.exit("Instagram took over 10 minutes to process the video")
    done = requests.post(f"{GRAPH}/{ig_id}/media_publish", data={"creation_id": made["id"], "access_token": token},
                         timeout=60).json()
    if "id" not in done:
        sys.exit(f"publish failed: {done}")
    return done["id"]


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--go"]
    if len(args) < 3:
        sys.exit(__doc__)
    page, video, caption = args[0].upper(), Path(args[1]), args[2]
    comment = args[3] if len(args) > 3 else ""
    page_id, token = env_key(f"{page}_FB_PAGE_ID"), env_key(f"{page}_FB_PAGE_TOKEN")
    if not page_id or not token:
        sys.exit(f"missing {page}_FB_PAGE_ID or {page}_FB_PAGE_TOKEN")
    ig_id, name = ig_account(page_id, token)
    print(f"Instagram account: @{name} ({ig_id})")
    if "--go" not in sys.argv:
        print("dry run: nothing posted")
        return
    media = upload_reel(ig_id, token, video, caption)
    link = requests.get(f"{GRAPH}/{media}", params={"fields": "permalink", "access_token": token}, timeout=60).json()
    print("posted:", link.get("permalink", media))
    if comment:
        r = requests.post(f"{GRAPH}/{media}/comments", data={"message": comment, "access_token": token}, timeout=60)
        print("comment:", r.json().get("id", r.text[:200]))


if __name__ == "__main__":
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    main()
