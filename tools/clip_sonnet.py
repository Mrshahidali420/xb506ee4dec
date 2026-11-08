"""Twice a week, one clip build with Sonnet picking the scenes, run from this PC. Skips quietly when it cannot.

  python tools/clip_sonnet.py            started by Windows Task Scheduler (daily/clips-sonnet.ps1)

Skips (exit 0, nothing changed) when: this week already has 2 Sonnet builds, claude.exe is missing, Sonnet does not
answer a tiny test question (plan used up, or no network), or there is no new trending episode. The GitHub run
(clips-daily.yml) keeps going on its own without this. A Sonnet build is the same as the GitHub one, except that
Sonnet reads the speech text, looks at the picture sheets and searches the web to pick the scenes (daily/CLIPWRITER.md).
Steps: pick the episode -> prep on GitHub (download, speech, cuts, sheets) -> Sonnet writes clips.json -> cut on GitHub
and hand over to the posters -> record the episode as done.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE = Path.home() / ".local" / "bin" / "claude.exe"
MODEL = "claude-sonnet-5-5"
PER_WEEK = 2
COUNT = ROOT / "daily" / "logs" / "clips-sonnet.json"
PY = [sys.executable, "-I"]


def say(text: str) -> None:
    print(f"{time.strftime('%F %T')} {text}", flush=True)


def run(*args: str, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def sonnet(prompt: str, timeout: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}
    return subprocess.run([str(CLAUDE), "-p", prompt, "--model", MODEL, "--permission-mode", "bypassPermissions"],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)


def main() -> int:
    week = time.strftime("%G-W%V")
    counts = json.loads(COUNT.read_text()) if COUNT.exists() else {}
    if counts.get(week, 0) >= PER_WEEK:
        say(f"skip: {counts[week]} Sonnet builds already in {week}")
        return 0
    if not CLAUDE.exists():
        say("skip: claude.exe not found")
        return 0
    try:
        test = sonnet("Reply with the single word: ready", 180)
    except subprocess.TimeoutExpired:
        say("skip: Sonnet did not answer in time")
        return 0
    if test.returncode != 0 or "ready" not in test.stdout.lower():
        say(f"skip: Sonnet is not available ({(test.stdout + test.stderr).strip()[:200]})")
        return 0

    run("git", "pull", "-q", "--rebase", "--autostash")  # take the GitHub run's state, so one episode is never clipped twice
    picked = run(*PY, "tools/clip_daily.py", "pick")
    job = next((l[4:] for l in picked.stdout.splitlines() if l.startswith("job=")), "")
    if not job:
        say(f"skip: no new trending episode ({picked.stderr.strip()[-200:]})")
        return 0
    say(f"episode: {job}")
    prep = run(*PY, "tools/clip_job.py", "prepjob", job, timeout=3 * 3600)
    if prep.returncode != 0:
        say(f"prep failed: {(prep.stdout + prep.stderr).strip()[-300:]}")
        run(*PY, "tools/clip_daily.py", "mark", job, "fail")
        return 1
    writer = sonnet(f"Read daily/CLIPWRITER.md and follow it for the job {job}. Write {job}/clips.json.", 40 * 60)
    if writer.returncode != 0 or not (ROOT / job / "clips.json").exists():
        say(f"Sonnet wrote no clips ({(writer.stdout + writer.stderr).strip()[-300:]})")
        return 1
    say(f"Sonnet's picks: {writer.stdout.strip()[-400:]}")
    cut = run(*PY, "tools/clip_job.py", "cut", job, "--publish", timeout=3 * 3600)
    if cut.returncode != 0:
        say(f"cut failed: {(cut.stdout + cut.stderr).strip()[-300:]}")
        run(*PY, "tools/clip_daily.py", "mark", job, "fail")
        return 1
    run(*PY, "tools/clip_daily.py", "mark", job, "done")
    run("git", "add", "autopilot")
    run("git", "commit", "-qm", f"Clips: {job} done (Sonnet)")
    run("git", "pull", "-q", "--rebase", "--autostash")
    run("git", "push", "-q")
    counts[week] = counts.get(week, 0) + 1
    COUNT.parent.mkdir(parents=True, exist_ok=True)
    COUNT.write_text(json.dumps(counts))
    say(f"done: {job} ({counts[week]} of {PER_WEEK} this week)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
