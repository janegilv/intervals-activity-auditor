import csv
from pathlib import Path

def write(findings, path: Path):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "check","severity","summary","activity_ids","activity_names","activity_urls","reasons"
        ])
        w.writeheader()
        for x in findings:
            w.writerow({
                "check": x["check"],
                "severity": x["severity"],
                "summary": x["summary"],
                "activity_ids": " | ".join(a["id"] for a in x["activities"]),
                "activity_names": " | ".join(a["name"] for a in x["activities"]),
                "activity_urls": " | ".join(a["url"] for a in x["activities"]),
                "reasons": " | ".join(str(r) for r in x["reasons"]),
            })
