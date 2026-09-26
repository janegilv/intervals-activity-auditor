import csv
import json
from pathlib import Path

from intervals_auditor.views import ActivityView

FIELDS = [
    "activity_id", "url", "start_local", "type", "name", "duration_seconds", "distance",
    "check", "severity", "state", "summary", "related_activity_ids", "details",
    "first_seen_at", "ack_note", "ack_created_at",
]


def write(views: list[ActivityView], path: Path, generated_at: str):
    """One row per issue, activity columns repeated so it sorts/filters well."""
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for view in views:
            a = view.activity
            for i in view.issues:
                w.writerow({
                    "activity_id": view.activity_id,
                    "url": view.url,
                    "start_local": a.start_date_local if a else "",
                    "type": a.activity_type if a else "",
                    "name": a.name if a else "",
                    "duration_seconds": a.duration_seconds if a else "",
                    "distance": a.distance if a else "",
                    "check": i.check,
                    "severity": i.severity,
                    "state": i.state,
                    "summary": i.summary,
                    "related_activity_ids": " ".join(i.related_activity_ids),
                    "details": json.dumps(i.details, default=str),
                    "first_seen_at": i.first_seen_at or "",
                    "ack_note": i.ack.note if i.ack else "",
                    "ack_created_at": i.ack.created_at if i.ack else "",
                })
