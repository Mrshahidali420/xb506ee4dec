"""Posting queue: finished videos wait as release assets, GitHub posts one at a time.

  python queue_post.py add <page> <video.mp4> "<caption>"   (on the PC) upload + add to the queue
  python queue_post.py next <page> [--go]                   (on GitHub) post the next video if the gap allows
  python queue_post.py status <page>

The queue list is queue/<page>.json in the repo; the videos are assets of the
release "queue-<page>". A posted video's asset is deleted; its line stays as the record.
MIN_GAP_MINUTES (default 115) keeps posts apart even if the schedule fires early or twice.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPOSITORY", "Mrshahidali420/xb506ee4dec")


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args, "-R", REPO], check=True, capture_output=True, text=True).stdout


def load(page: str) -> tuple[Path, dict]:
    path = ROOT / "queue" / f"{page}.json"
    return path, (json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"items": []})


def save(path: Path, queue: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def add(page: str, video: Path, caption: str) -> None:
    tag = f"queue-{page}"
    if subprocess.run(["gh", "release", "view", tag, "-R", REPO], capture_output=True).returncode:
        gh("release", "create", tag, "--title", tag, "--notes", "Posting queue. Assets are deleted once posted.")
    asset = f"{int(time.time())}-{video.name}"
    staged = video.with_name(asset)
    staged.write_bytes(video.read_bytes())
    try:
        gh("release", "upload", tag, str(staged))
    finally:
        staged.unlink()
    path, queue = load(page)
    queue["items"].append({"asset": asset, "caption": caption, "added": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "posted_at": None, "post": None})
    save(path, queue)
    print(f"queued {asset}; {sum(1 for i in queue['items'] if not i['posted_at'])} waiting")


def next_post(page: str, go: bool) -> None:
    path, queue = load(page)
    waiting = [i for i in queue["items"] if not i["posted_at"]]
    posted = [i["posted_at"] for i in queue["items"] if i["posted_at"]]
    print(f"{len(waiting)} waiting")
    if not waiting:
        print("::warning::queue is empty, nothing to post")
        return
    gap = float(os.environ.get("MIN_GAP_MINUTES") or 115)
    if posted:
        since = (datetime.now(timezone.utc) - datetime.fromisoformat(max(posted))).total_seconds() / 60
        if since < gap:
            print(f"last post {since:.0f} min ago, gap is {gap:.0f}: waiting")
            return
    item = waiting[0]
    print(f"next: {item['asset']}\ncaption: {item['caption']}")
    if not go:
        print("dry run: nothing posted")
        return
    sys.path.insert(0, str(ROOT / "tools"))
    import post_fb

    page_id, token = post_fb.env_key(f"{page.upper()}_FB_PAGE_ID"), post_fb.env_key(f"{page.upper()}_FB_PAGE_TOKEN")
    gh("release", "download", f"queue-{page}", "-p", item["asset"], "-D", "/tmp", "--clobber")
    video_id = post_fb.upload(page_id, token, Path("/tmp") / item["asset"], item["caption"])
    item["posted_at"], item["post"] = datetime.now(timezone.utc).isoformat(timespec="seconds"), video_id
    save(path, queue)
    gh("release", "delete-asset", f"queue-{page}", item["asset"], "-y")
    print(f"posted https://www.facebook.com/{video_id}; {len(waiting) - 1} left")
    if len(waiting) - 1 <= 3:
        print(f"::warning::only {len(waiting) - 1} videos left in the {page} queue")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, page = sys.argv[1], sys.argv[2].lower()
    if cmd == "add" and len(sys.argv) == 5:
        add(page, Path(sys.argv[3]), sys.argv[4])
    elif cmd == "next":
        next_post(page, "--go" in sys.argv)
    elif cmd == "status":
        path, queue = load(page)
        for i in queue["items"]:
            print(("posted " + i["posted_at"]) if i["posted_at"] else "waiting", "|", i["asset"])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
