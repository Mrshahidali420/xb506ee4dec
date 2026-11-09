"""Shared helpers for the lab: time one tool, catch its failure, write a result record."""
import json
import os
import time
import traceback
from importlib import metadata
from pathlib import Path

OUT = Path(os.environ.get("LAB_OUT", "out"))


def pkg(*names):
    found = []
    for n in names:
        try:
            found.append(f"{n}=={metadata.version(n)}")
        except Exception:
            pass
    return ", ".join(found)


def install_tail(n=1200):
    log = os.environ.get("INSTALL_LOG")
    if log and Path(log).exists():
        return "\n--- install log tail ---\n" + Path(log).read_text(errors="replace")[-n:]
    return ""


def run_tool(group, name, fn, pin=None):
    """fn(dir) -> (list of file names written in dir, note). Records seconds, output and the error text."""
    d = OUT / group
    d.mkdir(parents=True, exist_ok=True)
    rec = {"group": group, "tool": name, "ok": False, "seconds": None, "output": "", "error": "", "note": "", "pin": ""}
    start = time.time()
    try:
        files, note = fn(d)
        rec.update(ok=True, output=", ".join(files), note=note or "")
    except BaseException:  # one broken tool must never stop the others
        rec["error"] = traceback.format_exc()[-1500:] + install_tail()
    rec["seconds"] = round(time.time() - start, 1)
    try:
        rec["pin"] = pin() if pin else ""
    except Exception:
        pass
    (d / f"{name}.result.json").write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec, indent=1))
    return rec
