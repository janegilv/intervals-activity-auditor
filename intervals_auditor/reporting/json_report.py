import json
from pathlib import Path

from intervals_auditor.views import ActivityView


def activity_to_dict(view: ActivityView) -> dict:
    a = view.activity
    return {
        "id": view.activity_id,
        "url": view.url,
        "name": a.name if a else None,
        "type": a.activity_type if a else None,
        "start_local": a.start_date_local if a else None,
        "start": a.start_date if a else None,
        "duration_seconds": a.duration_seconds if a else None,
        "distance": a.distance if a else None,
        "deleted": a is None,
        "issues": [
            {
                "number": i.number,
                "key": i.key,
                "check": i.check,
                "severity": i.severity,
                "state": i.state,
                "summary": i.summary,
                "related": [{"id": rid, "url": url} for rid, url in i.related_urls],
                "details": i.details,
                "first_seen_at": i.first_seen_at,
                "reopen_reason": i.reopen_reason,
                "acknowledgment": None if i.ack is None else {
                    "id": i.ack.id, "note": i.ack.note, "created_at": i.ack.created_at,
                },
            }
            for i in view.issues
        ],
    }


def write(views: list[ActivityView], path: Path, generated_at: str):
    payload = {
        "generated_at": generated_at,
        "activity_count": len(views),
        "issue_count": sum(len(v.issues) for v in views),
        "activities": [activity_to_dict(v) for v in views],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
