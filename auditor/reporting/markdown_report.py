from pathlib import Path

def write(findings, path: Path):
    lines = ["# Intervals.icu Activity Audit", ""]
    if not findings:
        lines += ["No findings.", ""]
    for i, x in enumerate(findings, 1):
        lines += [f"## {i}. [{x['severity'].upper()}] {x['check']}", "", x["summary"], "", "**Activities:**"]
        for a in x["activities"]:
            lines.append(f"- **{a['name']}** (`{a['id']}`) — {a['type']} — {a['start']} — {a['end']} — [Investigate]({a['url']})")
            #import sys
            #import pprint
            #pprint.pprint(a)
            #sys.exit(1)
        lines += ["", "**Why flagged:**"]
        for r in x["reasons"]:
            lines.append(f"- `{r['rule']}`: " + ", ".join(f"{k}={v}" for k, v in r.items() if k != "rule"))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
