"""Post the next show-clip reel of a Page: Facebook Reel, then Instagram Reel on the linked account.

Usage: GH_TOKEN=<ARCHIVE_TOKEN> python tools/post_clips.py <page> [--go]      (page: animerewind, manhwax, spoiler)
Reads the manifest clips/<page>.json of the private recap-assets repo (tools/publish_clips.py writes it), takes the
oldest clip not in the ledger queue/<page>-clips.json (a different show from the last post when there is one),
and posts it. Without --go it only prints what it would post.
  - caption: the clip's caption; first comment: our recap of the show when queue/<queue>.json has one, else the show's AniList page
  - Instagram gets the short version; its caption says "link in bio". An Instagram failure never stops the Facebook post.
  - the ledger keeps the gap (MIN_GAP_MINUTES_CLIPS, 50) from its own timestamps, so a clip is never posted twice
  - a rate-limit or block answer from Meta pauses the Page (an hour or a day) and is logged; nothing more is sent
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import clip_pages  # noqa: E402
import post_fb  # noqa: E402

MAX_FAILS = 2           # Facebook refusals after which a clip is left alone
REEL_MAX = 85            # seconds the full version may last to go out as a Reel (the limit is 90; the short version is under 75)
THROTTLE_CODES = {4, 17, 32, 341, 613}
BLOCK_CODES = {368}
THROTTLE_PAUSE = 3600
BLOCK_PAUSE = 86400
WORK = Path(os.environ.get("RUNNER_TEMP", "/tmp"))


def gh(*args: str, raw: bool = False) -> str | bytes:
    out = subprocess.run(["gh", *args], capture_output=True, check=True)
    return out.stdout if raw else out.stdout.decode()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def age_seconds(stamp: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()


def ledger_path(page: str) -> Path:
    return ROOT / "queue" / f"{page}-clips.json"


def load_ledger(page: str) -> dict:
    f = ledger_path(page)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"posted": [], "paused_until": None, "note": ""}


def save_ledger(page: str, ledger: dict) -> None:
    ledger_path(page).parent.mkdir(exist_ok=True)
    ledger_path(page).write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def manifest(repo: str, page: str) -> list[dict]:
    try:
        got = json.loads(gh("api", f"repos/{repo}/contents/clips/{page}.json"))
    except subprocess.CalledProcessError:
        return []
    return json.loads(base64.b64decode(got["content"]))["items"]


def download(repo: str, asset: int, name: str) -> Path:
    dest = WORK / name
    dest.write_bytes(gh("api", "-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{asset}", raw=True))
    return dest


def next_item(items: list[dict], ledger: dict) -> dict | None:
    """The oldest clip not posted yet; one from another show than the last post comes first."""
    done = {p["id"] for p in ledger["posted"]}
    failed = {i for i, n in ledger.get("failed", {}).items() if n >= MAX_FAILS}
    for i in sorted(failed & {i["id"] for i in items} - done):
        print(f"skipping {i}: Facebook refused it {ledger['failed'][i]} times")
    waiting = sorted((i for i in items if i["id"] not in done | failed), key=lambda i: i["made_at"])
    last = ledger["posted"][-1].get("show") if ledger["posted"] else None
    return next((i for i in waiting if i["anime"] != last), waiting[0] if waiting else None)


def recap_link(conf: dict, title: str) -> str | None:
    """Our own recap post of this show (queue/<queue>.json, matched by the show's name), the newest one."""
    f = ROOT / "queue" / f"{conf['queue']}.json"
    key = clip_pages.slug(title)
    if not f.exists() or not key:
        return None
    for it in reversed(json.loads(f.read_text(encoding="utf-8"))["items"]):
        if it.get("post") and f"-{key}-" in f"-{clip_pages.slug(' '.join([it.get('job', ''), it.get('asset', ''), it.get('caption', '')]))}-":
            return f"https://www.facebook.com/{it['post']}"
    return None


def first_comment(conf: dict, item: dict) -> str:
    recap = recap_link(conf, item["anime"])
    if recap:
        return f"Our full recap of {item['anime']}: {recap}"
    return f"{item['anime']}: {item['link']}" if item.get("link") else ""


def pause_for(exc: post_fb.GraphError) -> int:
    text = str(exc).lower()
    if exc.code in BLOCK_CODES or any(w in text for w in ("blocked", "spam", "too fast")):
        return BLOCK_PAUSE
    if exc.code in THROTTLE_CODES or 80000 <= exc.code <= 80014 or any(w in text for w in ("rate limit", "too many", "limit reached")):
        return THROTTLE_PAUSE
    return 0


def post_instagram(page_id: str, token: str, video: Path, caption: str) -> str:
    import post_ig

    ig_id, name = post_ig.ig_account(page_id, token)
    media = post_ig.upload_reel(ig_id, token, video, caption)
    link = post_ig.requests.get(f"{post_ig.GRAPH}/{media}", params={"fields": "permalink", "access_token": token}, timeout=60).json()
    return link.get("permalink", f"{name}:{media}")


def run(page: str, go: bool) -> None:
    conf = clip_pages.PAGES[page]
    repo = f"{os.environ.get('OWNER', 'Mrshahidali420')}/recap-assets"
    ledger = load_ledger(page)
    ledger.setdefault("failed", {})
    if ledger.get("paused_until") and time.time() < float(ledger["paused_until"]):
        print(f"paused by Meta's limits until {datetime.fromtimestamp(float(ledger['paused_until']), timezone.utc):%Y-%m-%d %H:%M} UTC: {ledger.get('note', '')}")
        return
    gap = float(os.environ.get("MIN_GAP_MINUTES_CLIPS") or 50)
    if ledger["posted"] and age_seconds(ledger["posted"][-1]["at"]) / 60 < gap:
        print(f"last post {age_seconds(ledger['posted'][-1]['at']) / 60:.0f} min ago, gap is {gap:.0f}: waiting")
        return
    items = manifest(repo, page)
    item = next_item(items, ledger)
    waiting = sum(1 for i in items if i["id"] not in {p["id"] for p in ledger["posted"]})
    if not item:
        print(f"::warning::no clip waiting for {page}")
        return
    use_full = item["full_seconds"] <= REEL_MAX
    comment = first_comment(conf, item)
    print(f"{waiting} clip(s) waiting. next: {item['id']} ({item['anime']} episode {item['episode']})")
    print(f"facebook reel: {'full' if use_full else 'short'} version, {item['full_seconds'] if use_full else item['short_seconds']} s")
    print(f"caption: {item['caption']}")
    print(f"first comment: {comment or '(none)'}")
    print(f"instagram reel: short version, {item['short_seconds']} s, caption ends with 'link in bio' (posts only when IG_POST_ENABLED is true)")
    if not go:
        print("dry run: nothing posted")
        return
    page_id, token = post_fb.env_key(f"{conf['fb']}_FB_PAGE_ID"), post_fb.env_key(f"{conf['fb']}_FB_PAGE_TOKEN")
    if not page_id or not token:
        sys.exit(f"missing {conf['fb']}_FB_PAGE_ID or {conf['fb']}_FB_PAGE_TOKEN")
    video = download(repo, item["full_id"] if use_full else item["short_id"], "reel_fb.mp4")   # before the claim: a failed download costs nothing
    entry = {"id": item["id"], "show": item["anime"], "at": now_iso(), "fb": None, "ig": None}
    ledger["posted"] = (ledger["posted"] + [entry])[-300:]
    save_ledger(page, ledger)   # claimed before the upload: a crash after Facebook publishes can never post this clip twice
    try:
        entry["fb"] = post_fb.reel(page_id, token, video, item["caption"])
    except post_fb.GraphError as exc:
        ledger["posted"] = [p for p in ledger["posted"] if p is not entry]   # a clean refusal: give the claim back
        wait = pause_for(exc)
        if not wait:
            ledger["failed"][item["id"]] = ledger["failed"].get(item["id"], 0) + 1
            print(f"::warning::{item['id']} failed {ledger['failed'][item['id']]} of {MAX_FAILS} times")
        if wait:   # Meta's limit: stop, say so, and let the Page rest
            ledger["paused_until"], ledger["note"] = time.time() + wait, str(exc)[:200]
            save_ledger(page, ledger)
            print(f"::warning::Meta limit, {page} paused {wait // 3600} h: {exc}")
            return
        save_ledger(page, ledger)
        sys.exit(f"facebook reel failed: {exc}")
    save_ledger(page, ledger)   # the reel id is in the claimed entry
    print(f"posted https://www.facebook.com/{entry['fb']}")
    if comment:
        try:
            print("comment:", post_fb.requests.post(f"{post_fb.GRAPH}/{entry['fb']}/comments", data={"message": comment, "access_token": token}, timeout=60).json())
        except Exception as exc:
            print(f"::warning::comment failed: {exc}")
    if os.environ.get("IG_POST_ENABLED") == "true":
        try:
            entry["ig"] = post_instagram(page_id, token, download(repo, item["short_id"], "reel_ig.mp4"), f"{item['caption']}\n\nFull recap: link in bio")
            print(f"instagram: {entry['ig']}")
        except BaseException as exc:  # sys.exit inside post_ig or an API error: Facebook is up, keep going
            print(f"::warning::Instagram failed: {exc}")
        save_ledger(page, ledger)


if __name__ == "__main__":
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    if len(sys.argv) < 2 or sys.argv[1] not in clip_pages.PAGES:
        sys.exit(__doc__)
    run(sys.argv[1], "--go" in sys.argv)
