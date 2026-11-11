"""Hand the finished clips to the Pages' posters: files go to the private recap-assets repo, a manifest lists them.

Usage: GH_TOKEN=<ARCHIVE_TOKEN> python tools/publish_clips.py <job>
Reads <job>/clips_out.json (tools/cut_clip.py) and <job>/clip.json. For each Page:
  - uploads <page>_clip_NN.mp4 and <page>_short_NN.mp4 to the release `clips` of <owner>/recap-assets
  - adds the clip to clips/<page>.json in that repo (items: ids, texts, asset ids and sizes)
The posters (where-build, mi-build, and tools/post_clips.py for Anime Rewind, Manhwa X, Spoiler Room) read that manifest, post, and keep their own ledger. Items older than
KEEP_DAYS are removed with their files, so the store never fills up.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

TAG = "clips"
KEEP_DAYS = 5


def gh(*args: str, data: dict | None = None) -> dict | list | None:
    cmd = ["gh", "api", *args] + (["--input", "-"] if data is not None else [])
    out = subprocess.run(cmd, input=json.dumps(data) if data is not None else None, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"gh api {args[0]} failed: {out.stderr.strip()[:300]}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def asset_id(repo: str, name: str) -> int:
    rel = gh(f"repos/{repo}/releases/tags/{TAG}")
    return next(a["id"] for a in rel["assets"] if a["name"] == name)


def upload(repo: str, path: Path, name: str) -> int:
    tmp = path.with_name(name)
    if tmp != path:
        tmp.write_bytes(path.read_bytes())
    subprocess.run(["gh", "release", "upload", TAG, str(tmp), "-R", repo, "--clobber"], check=True)
    if tmp != path:
        tmp.unlink()
    return asset_id(repo, name)


def manifest(repo: str, page: str) -> tuple[dict, str | None]:
    try:
        got = gh(f"repos/{repo}/contents/clips/{page}.json")
        return json.loads(base64.b64decode(got["content"])), got["sha"]
    except RuntimeError:
        return {"items": []}, None


def save(repo: str, page: str, data: dict, sha: str | None) -> None:
    body = {"message": f"clips: {page}", "content": base64.b64encode(json.dumps(data, indent=1).encode()).decode()}
    if sha:
        body["sha"] = sha
    gh(f"repos/{repo}/contents/clips/{page}.json", "-X", "PUT", data=body)


def prune(repo: str, items: list[dict]) -> list[dict]:
    keep, cutoff = [], time.time() - KEEP_DAYS * 86400
    for it in items:
        if it["made_at"] >= cutoff:
            keep.append(it)
            continue
        for key in ("full_id", "short_id"):
            try:
                gh(f"repos/{repo}/releases/assets/{it[key]}", "-X", "DELETE")
            except RuntimeError as err:
                print(f"old file not removed: {err}", flush=True)
    return keep


def main() -> None:
    job = Path(sys.argv[1])
    meta = json.loads((job / "clip.json").read_text(encoding="utf-8"))
    clips = json.loads((job / "clips_out.json").read_text(encoding="utf-8"))
    if not clips:
        sys.exit("no clip kept")   # every stretch was dropped (recap, preview, credits): the title counts as failed, not done
    repo = f"{os.environ.get('OWNER', 'Mrshahidali420')}/recap-assets"
    if subprocess.run(["gh", "release", "view", TAG, "-R", repo], capture_output=True).returncode:
        subprocess.run(["gh", "release", "create", TAG, "-R", repo, "--title", TAG, "--notes", "Clips waiting for the Page posters."], check=True)
    got = job / "work" / "audio.txt"   # what fetch_source really got (dub or sub); else what the pick asked for
    audio = got.read_text(encoding="utf-8").strip() if got.exists() else meta.get("audio")
    print(f"audio: {audio or 'unknown'}", flush=True)
    for page in meta.get("pages") or [meta["page"]]:
        data, sha = manifest(repo, page)
        data["items"] = prune(repo, data["items"])
        for i, clip in enumerate(clips, 1):
            f = clip["files"][page]
            base = f"{job.name}-{page}-{i:02d}"
            item = {"id": base, "page": page, "anilist_id": meta["manga_id"] if page in ("manhwaindex", "manhwax") else meta["anilist_id"], "anime": meta["title"], "episode": meta["episode"],
                    "link": meta.get("link") or ("https://whereanime.com/" if page == "manhwaindex" and not meta.get("manga_id") else None), "movie": bool(meta.get("movie")),
                    "title": clip["title"], "caption": clip["caption"], "comment": clip["comment"], "why": clip.get("why", ""),
                    "full_id": upload(repo, job / f["full"], f"{base}-full.mp4"), "short_id": upload(repo, job / f["short"], f"{base}-short.mp4"),
                    "full_seconds": round(clip["end"] - clip["start"]), "short_seconds": round(clip["short_end"] - clip["short_start"]),
                    "audio": audio, "made_at": int(time.time())}
            data["items"] = [x for x in data["items"] if x["id"] != base] + [item]
            print(f"published {base}", flush=True)
        save(repo, page, data, sha)


if __name__ == "__main__":
    main()
