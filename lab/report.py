"""Build results.md from every *.result.json under the given folder (python report.py DIR [PINS_FILE])."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
recs = [json.loads(p.read_text()) for p in sorted(root.rglob("*.result.json"))]
pins = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 and Path(sys.argv[2]).exists() else ""
lines = ["# Lab results", "", "| group | tool | worked? | seconds | output | note |", "|---|---|---|---|---|---|"]
for r in recs:
    note = (r["note"] or "").replace("|", "/").replace("\n", " ")
    lines.append(f"| {r['group']} | {r['tool']} | {'yes' if r['ok'] else 'NO'} | {r['seconds']} | {r['output']} | {note} |")
fails = [r for r in recs if not r["ok"]]
if fails:
    lines += ["", "## Failures"]
    for r in fails:
        lines += ["", f"### {r['tool']}", "```", r["error"], "```"]
lines += ["", "## Versions and pins", ""]
lines += [f"- {r['tool']}: {r['pin']}" for r in recs if r["pin"]]
lines += ["", pins]
(root / "results.md").write_text("\n".join(lines))
print("\n".join(lines[:40]))
