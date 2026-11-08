"""Picture posts (carousels) for our pages, next to the reels.

  python post_extra.py add <page> <dir>        queue the cards in <dir> (card_*.jpg + post.json), dir is under extra/
  python post_extra.py next <page> [--go]      post the next queued carousel if the gap allows (on GitHub)
  python post_extra.py thumbs <page> <job> <youtube link>   queue each of the job's YouTube thumbnails (thumb-*.jpg)
                                               as its own Facebook photo post with the link (Facebook only)

Instagram gets the whole carousel; Facebook gets, in turn: the title cover, one panel card, a text question.

The queue is queue/<page>-extra.json. The cards live in the repo (extra/<page>/<name>/), so Instagram can fetch
them from raw.githubusercontent.com. EXTRA_GAP_MINUTES (default 240) keeps carousels apart.
"""
from __future__ import annotations

import json
import os
import shutil
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
    kinds = ["cover", "panel", "text"]  # what Facebook gets, in turn (Instagram always gets the carousel)
    fb = kinds[len(queue["items"]) % len(kinds)]
    fb = "panel" if fb == "cover" and not post.get("cover") else fb
    queue["items"].append({"dir": folder, "caption": post["caption"], "comment": post.get("comment", ""),
                           "cards": post["cards"], "fb": fb, "text": post.get("text", post["caption"]), "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "posted_at": None, "post": None})
    save(page, queue)
    print(f"queued {folder}")


def add_thumbs(page: str, job: str, link: str) -> None:
    """Every thumbnail made for a long video is a picture post that sends people to it (Shahid, 8 Nov 2026).
    The images are copied to extra/<page>/<job>-thumbs/ so the queue never depends on the job folder."""
    src = ROOT / job
    title = (src / "yt" / "title.txt").read_text(encoding="utf-8").strip() if (src / "yt" / "title.txt").exists() else src.name
    folder = f"extra/{page}/{src.name}-thumbs"
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
    queue = load(page)
    for style in ("scene", "split", "shock"):
        img = src / f"thumb-{style}.jpg"
        if not img.exists() or any(i.get("file") == f"{folder}/{img.name}" for i in queue["items"]):
            continue
        shutil.copy(img, ROOT / folder / img.name)
        queue["items"].append({"kind": "photo", "dir": folder, "file": f"{folder}/{img.name}", "cards": 1,
                               "caption": f"{title}\n\nWatch the full recap on YouTube 👉 {link}",
                               "comment": f"Full video: {link}", "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "posted_at": None, "post": None})
        print(f"queued {img.name}")
    save(page, queue)


def post_facebook(page_id: str, token: str, item: dict) -> str:
    """Facebook cannot swipe a page post (many photos show as a grid), so it gets one thing per turn:
    the official title cover, one panel card, or a text question. Instagram gets the whole carousel."""
    if item.get("kind") == "photo":  # a YouTube thumbnail with the video link
        with (ROOT / item["file"]).open("rb") as fh:
            r = post_fb.check(requests.post(f"{GRAPH}/{page_id}/photos", data={"message": item["caption"], "published": "true",
                                                                               "access_token": token},
                                            files={"source": fh}, timeout=120), "photo post")
        return r.get("post_id") or r["id"]
    kind = item.get("fb", "panel")
    if kind == "text":
        return post_fb.check(requests.post(f"{GRAPH}/{page_id}/feed", data={"message": item["text"], "access_token": token},
                                           timeout=120), "text post")["id"]
    name = "cover.jpg" if kind == "cover" else "card_1.jpg"
    with (ROOT / item["dir"] / name).open("rb") as fh:
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
    if os.environ.get("IG_POST_ENABLED") == "true" and item.get("kind") != "photo":  # an Instagram link cannot be clicked
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
    elif cmd == "thumbs" and len(sys.argv) == 5:
        add_thumbs(page, sys.argv[3], sys.argv[4])
    elif cmd == "next":
        next_post(page, "--go" in sys.argv)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
