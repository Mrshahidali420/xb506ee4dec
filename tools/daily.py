"""Daily video run: the mechanical parts, so the Claude run only writes the scripts.

python tools/daily.py prep    make today's job folders from daily/plan.json, start the prep builds on GitHub,
                              wait for them, download only the small sheets (no video ever comes to this PC)
python tools/daily.py build   push the written scripts and start the real builds, queued to each page
python tools/daily.py post    ask GitHub to post each page's next queued video (queue_post.py keeps the gap)

daily/plan.json  pages -> slots. A slot is a series (next episode or part), a movie pool, or a promo.
daily/today.json today's jobs: job, page, kind, prep run, state (prep/ready/failed/built).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN, TODAY = ROOT / "daily" / "plan.json", ROOT / "daily" / "today.json"
PAGES = ("anime", "spoiler", "manhwax")
WAIT_MINUTES = 90


def sh(*args: str, check: bool = True) -> str:
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} failed: {out.stderr.strip()[-600:]}")
    return out.stdout.strip()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def queue_items(page: str) -> list[dict]:
    return load(ROOT / "queue" / f"{page}.json").get("items", [])


def series_job(slot: dict) -> tuple[str, dict] | None:
    n = slot["next"]
    if n > slot.get("last", 10**6):
        return None
    job = slot["job"].format(n=n)
    if slot["type"] == "manga":
        per = slot["chapters_per_part"]
        return job, {"type": "manga", "chapters": f"{(n - 1) * per + 1}-{n * per}", "sources": slot["sources"]}
    if slot["type"] == "anime":
        return job, {"type": "anime", "title": slot["title"], "episode": n, "lang": slot.get("lang", "dub")}
    return job, {"type": "kisskh", "url": slot["url"], "episode": n, "shot_sheets": True}


def movie_job(slot: dict) -> tuple[str, dict] | None:
    title = next((t for t in slot["pool"] if t not in slot["used"]), None)
    if title is None:
        return None
    if slot["source"] == "anime":
        return f"jobs/{slot['prefix']}-{slug(title)}", {"type": "anime", "title": title, "episode": 1, "lang": "dub"}
    return f"jobs/{slot['prefix']}-{slug(title)}", {"type": "kisskh", "search": title, "episode": 1, "shot_sheets": True}


def promo_job(page: str) -> tuple[str, dict, dict] | None:
    """Newest full video of this page that is posted and has no promo yet (any series, episode or movie)."""
    posted = [i for i in queue_items(page) if i.get("posted_at") and i.get("post") and i.get("job")]
    for item in sorted(posted, key=lambda i: i["posted_at"], reverse=True):
        job = item["job"]
        if job.endswith("-promo") or (ROOT / f"{job}-promo").exists() or not (ROOT / job / "script.json").exists():
            continue
        src = json.loads((ROOT / job / "source.json").read_text(encoding="utf-8"))
        return f"{job}-promo", src, {"parent": job, "parent_post": f"https://www.facebook.com/{item['post']}"}
    return None


def dispatch(job: str, queue: str = "") -> str:
    args = ["gh", "workflow", "run", "build.yml", "-f", f"job={job}"] + (["-f", f"queue={queue}"] if queue else [])
    url = sh(*args).splitlines()[-1]
    return url.rsplit("/", 1)[-1]


def push(message: str) -> None:
    sh("git", "add", "jobs")
    if sh("git", "diff", "--cached", "--name-only"):
        sh("git", "commit", "-qm", message)
    sh("git", "pull", "-q", "--rebase", "--autostash")
    sh("git", "push", "-q")


def wait(runs: dict[str, str]) -> dict[str, str]:
    """run id -> conclusion, polling until every run is done or the time is up."""
    done: dict[str, str] = {}
    end = time.time() + WAIT_MINUTES * 60
    while len(done) < len(runs) and time.time() < end:
        for rid in runs:
            if rid not in done:
                raw = sh("gh", "run", "view", rid, "--json", "status,conclusion", check=False)
                info = json.loads(raw) if raw.startswith("{") else {}  # a network blip: ask again next round
                if info.get("status") == "completed":
                    done[rid] = info["conclusion"]
        if len(done) < len(runs):
            time.sleep(45)
    return done


def prep() -> None:
    today = dt.date.today().isoformat()
    state = load(TODAY)
    if state.get("date") != today:
        if state.get("jobs") and any(j["state"] in ("ready", "prep") for j in state["jobs"]):
            print("WARNING: yesterday had unbuilt jobs:", [j["job"] for j in state["jobs"] if j["state"] != "built"])
        plan = load(PLAN)
        jobs = []
        for page in PAGES:
            for slot in plan[page]:
                kind = slot["kind"]
                extra: dict = {}
                if kind == "series":
                    made = series_job(slot)
                elif kind == "movie":
                    made = movie_job(slot)
                else:
                    found = promo_job(page)  # each promo slot takes the next posted video (its folder now exists)
                    made, extra = (found[:2], found[2]) if found else (None, {})
                if not made:
                    print(f"skip {page}/{kind}: nothing left or nothing posted yet")
                    continue
                job, src = made
                folder = ROOT / job
                folder.mkdir(parents=True, exist_ok=True)
                if not (folder / "source.json").exists():
                    save(folder / "source.json", src)
                ready = kind == "promo" or (folder / "script.json").exists()
                jobs.append({"job": job, "page": page, "kind": kind, "slot": plan[page].index(slot),
                             "state": "ready" if ready else "prep", **extra})
        state = {"date": today, "jobs": jobs}
        save(TODAY, state)
        push(f"Daily run {today}: job folders")
    todo = [j for j in state["jobs"] if j["state"] == "prep"]
    for j in todo:
        if not j.get("prep_run"):
            j["prep_run"] = dispatch(j["job"])
            print(f"prep started {j['job']} run {j['prep_run']}", flush=True)
    save(TODAY, state)
    results = wait({j["prep_run"]: j["job"] for j in todo})
    for j in todo:
        result = results.get(j["prep_run"], "timeout")
        if result != "success":
            j["state"], j["why"] = "failed", f"prep run {j['prep_run']}: {result}"
            continue
        sheets = ROOT / j["job"] / "sheets"
        sheets.mkdir(exist_ok=True)
        sh("gh", "run", "download", j["prep_run"], "-n", "sheets", "-D", str(sheets), check=False)
        j["state"] = "ready"
    save(TODAY, state)
    report(state)


def report(state: dict) -> None:
    print(f"\n== {state['date']} ==")
    for j in state["jobs"]:
        sheets = ROOT / j["job"] / "sheets"
        files = sorted(p.name for p in sheets.glob("*")) if sheets.exists() else []
        line = f"{j['state']:7} {j['page']:8} {j['kind']:7} {j['job']}"
        if j.get("parent"):
            line += f"  parent={j['parent']} post={j['parent_post']}"
        if j.get("why"):
            line += f"  ({j['why']})"
        if files:
            line += f"  sheets: {', '.join(files)}"
        print(line)


def build() -> None:
    state, plan = load(TODAY), load(PLAN)
    ready = [j for j in state["jobs"] if j["state"] == "ready"]
    missing = [j["job"] for j in ready if not (ROOT / j["job"] / "script.json").exists()]
    if missing:
        print("no script.json yet:", missing)
    ready = [j for j in ready if j["job"] not in missing]
    push(f"Daily run {state['date']}: scripts")
    for j in ready:
        j["build_run"] = dispatch(j["job"], j["page"])
        j["state"] = "built"
        slot = plan[j["page"]][j["slot"]]
        if j["kind"] == "series":
            slot["next"] += 1
        elif j["kind"] == "movie":
            src = json.loads((ROOT / j["job"] / "source.json").read_text(encoding="utf-8"))
            slot["used"].append(src.get("search") or src.get("title"))
        print(f"build started {j['job']} -> {j['page']} run {j['build_run']}", flush=True)
    for j in state["jobs"]:  # a movie that would not download is dropped from the pool, so tomorrow tries the next
        if j["state"] == "failed" and j["kind"] == "movie":
            slot = plan[j["page"]][j["slot"]]
            src = json.loads((ROOT / j["job"] / "source.json").read_text(encoding="utf-8"))
            slot["used"].append(src.get("search") or src.get("title"))
    save(TODAY, state)
    save(PLAN, plan)


def post() -> None:
    for page in PAGES:
        print(page, sh("gh", "workflow", "run", "post.yml", "-f", f"page={page}", "-f", "dry_run=false", check=False))


if __name__ == "__main__":
    {"prep": prep, "build": build, "post": post, "report": lambda: report(load(TODAY))}[sys.argv[1]]()
