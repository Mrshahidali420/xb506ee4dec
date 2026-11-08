"""Picture posts (carousels) for our pages, next to the reels.

  python post_extra.py add <page> <dir>        queue the cards in <dir> (card_*.jpg + post.json), dir is under extra/
  python post_extra.py next <page> [--go]      post the next queued carousel if the gap allows (on GitHub)

Instagram gets the whole carousel; Facebook gets one picture (the first card).

The queue is queue/<page>-extra.json. The cards live in the repo (extra/<page>/<name>/), so Instagram can fetch
them from raw.githubusercontent.com. EXTRA_GAP_MINUTES (default 240) keeps carousels apart.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()
sys.path.insert(0, str(Path(__file__).resolve().parent))
import post_fb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPOSITORY", "Mrshahidali420/xb506ee4dec")
GRAPH = post_fb.GRAPH
DEFAULT_COMMENT = "Which panel hit hardest? 👇"


def queue_path(page: str) -> Path:
    return ROOT / "queue" / f"{page}-extra.json"


def load(page: str) -> dict:
    path = queue_path(page)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []}


def save(page: str, queue: dict) -> None:
    queue_path(page).write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def add(page: str, folder: str) -> None:
    post = json.loads((ROOT / folder / "post.json").read_text(encoding="utf-8"))
    queue = load(page)
    if any(i["dir"] == folder for i in queue["items"]):
        print(f"{folder} is queued already")
        return
    queue["items"].append({"dir": folder, "caption": post["caption"], "comment": post.get("comment", ""),
                           "cards": post["cards"], "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "posted_at": None, "post": None})
    save(page, queue)
    print(f"queued {folder}")


def post_facebook(page_id: str, token: str, item: dict) -> str:
    """Facebook has no swipe carousel for a page post (many photos show as a grid), so it gets one picture: the hook card."""
    with (ROOT / item["dir"] / "card_1.jpg").open("rb") as fh:
        r = post_fb.check(requests.post(f"{GRAPH}/{page_id}/photos", data={"message": item["caption"], "published": "true",
                                                                           "access_token": token},
                                        files={"source": fh}, timeout=120), "photo post")
    return r.get("post_id") or r["id"]


def post_instagram(page_id: str, token: str, item: dict) -> str:
    """Carousel through the page's linked Instagram account; the cards are fetched from the repo."""
    import post_ig

    ig_id, name = post_ig.ig_account(page_id, token)
    kids = []
    for n in range(1, item["cards"] + 1):
        url = f"https://raw.githubusercontent.com/{REPO}/main/{item['dir']}/card_{n}.jpg"
        r = post_fb.check(requests.post(f"{GRAPH}/{ig_id}/media", data={"image_url": url, "is_carousel_item": "true",
                                                                         "access_token": token}, timeout=60), f"IG card {n}")
        kids.append(r["id"])
    parent = post_fb.check(requests.post(f"{GRAPH}/{ig_id}/media", data={"media_type": "CAROUSEL", "children": ",".join(kids),
                                                                          "caption": item["caption"], "access_token": token},
                                         timeout=60), "IG carousel")["id"]
    for _ in range(30):
        state = requests.get(f"{GRAPH}/{parent}", params={"fields": "status_code", "access_token": token}, timeout=60).json()
        if state.get("status_code") == "FINISHED":
            break
        if state.get("status_code") == "ERROR":
            sys.exit("Instagram could not process the carousel")
        time.sleep(5)
    media = post_fb.check(requests.post(f"{GRAPH}/{ig_id}/media_publish", data={"creation_id": parent, "access_token": token},
                                        timeout=60), "IG publish")["id"]
    link = requests.get(f"{GRAPH}/{media}", params={"fields": "permalink", "access_token": token}, timeout=60).json()
    print(f"Instagram @{name}: {link.get('permalink', media)}")
    return link.get("permalink", media)


def next_post(page: str, go: bool) -> None:
    queue = load(page)
    waiting = [i for i in queue["items"] if not i["posted_at"]]
    posted = [i["posted_at"] for i in queue["items"] if i["posted_at"]]
    print(f"{len(waiting)} carousels waiting")
    if not waiting:
        return
    gap = float(os.environ.get("EXTRA_GAP_MINUTES") or 240)
    if posted:
        since = (datetime.now(timezone.utc) - datetime.fromisoformat(max(posted))).total_seconds() / 60
        if since < gap:
            print(f"last carousel {since:.0f} min ago, gap is {gap:.0f}: waiting")
            return
    item = waiting[0]
    print(f"next: {item['dir']} ({item['cards']} cards)\ncaption: {item['caption'][:200]}")
    if not go:
        print("dry run: nothing posted")
        return
    page_id, token = post_fb.env_key(f"{page.upper()}_FB_PAGE_ID"), post_fb.env_key(f"{page.upper()}_FB_PAGE_TOKEN")
    post_id = post_facebook(page_id, token, item)
    item["posted_at"], item["post"] = datetime.now(timezone.utc).isoformat(timespec="seconds"), post_id
    save(page, queue)
    print(f"posted https://www.facebook.com/{post_id}")
    if os.environ.get("IG_POST_ENABLED") == "true":
        try:
            item["ig"] = post_instagram(page_id, token, item)
            save(page, queue)
        except BaseException as exc:  # Facebook is up; an Instagram failure must not undo the record
            print(f"::warning::Instagram failed: {exc}")
    import comment_fb

    try:
        comment_fb.comment(page, post_id, item.get("comment") or DEFAULT_COMMENT)
    except Exception as exc:
        print(f"::warning::comment failed: {exc}")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, page = sys.argv[1], sys.argv[2].lower()
    if cmd == "add" and len(sys.argv) == 4:
        add(page, sys.argv[3])
    elif cmd == "next":
        next_post(page, "--go" in sys.argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
