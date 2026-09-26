"""JSON shapes returned by the web API."""
from dataclasses import asdict

from intervals_auditor.models import Acknowledgment, activity_url
from intervals_auditor.reporting.json_report import activity_to_dict
from intervals_auditor.repositories import RunRecord
from intervals_auditor.services.check_settings import CheckSettings
from intervals_auditor.services.queries import ActivityDetail, StatusInfo
from intervals_auditor.views import ActivityView, IssueView


def ack_dict(ack: Acknowledgment) -> dict:
    return {
        "id": ack.id,
        "issue_number": ack.issue_number,
        "activity_id": ack.activity_id,
        "url": ack.url,
        "check": ack.check,
        "related_activity_ids": list(ack.related_activity_ids),
        "note": ack.note,
        "created_at": ack.created_at,
        "active": ack.is_active,
        "revoked_at": ack.revoked_at,
        "revoked_reason": ack.revoked_reason,
        "snapshot": ack.snapshot,
    }


def issue_dict(issue: IssueView) -> dict:
    return {
        "number": issue.number,
        "key": issue.key,
        "check": issue.check,
        "severity": issue.severity,
        "state": issue.state,
        "summary": issue.summary,
        "related": [{"id": rid, "url": url} for rid, url in issue.related_urls],
        "details": issue.details,
        "first_seen_at": issue.first_seen_at,
        "resolved_at": issue.resolved_at,
        "notified_at": issue.notified_at,
        "reopen_reason": issue.reopen_reason,
        "acknowledgment": None if issue.ack is None else {
            "id": issue.ack.id, "note": issue.ack.note, "created_at": issue.ack.created_at,
        },
    }


def activity_detail_dict(detail: ActivityDetail) -> dict:
    a = detail.activity
    base = activity_to_dict(ActivityView(detail.activity_id, a, []))
    base.pop("issues")
    if a is None:
        # Deleted: fall back to what an acknowledgment remembered about it.
        snap = next((ack.snapshot.get(detail.activity_id) for ack in detail.acks
                     if detail.activity_id in ack.snapshot), None) or {}
        base.update(name=snap.get("name"), type=snap.get("type"), start_local=snap.get("start_local"),
                    duration_seconds=snap.get("duration_seconds"), distance=snap.get("distance"))
    else:
        base.update(
            source=a.raw.get("source"), strava_id=a.raw.get("strava_id"), device=a.raw.get("device_name"),
            first_seen_at=a.first_seen_at, updated_at=a.updated_at, synced_at=a.synced_at,
            fingerprint=a.fingerprint,
        )
    base["url"] = activity_url(detail.activity_id)
    base["issues"] = [issue_dict(i) for i in detail.issues]
    base["acknowledgments"] = [ack_dict(ack) for ack in detail.acks]
    return base


def run_dict(run: RunRecord) -> dict:
    return asdict(run)


def status_dict(info: StatusInfo) -> dict:
    d = asdict(info)
    d["recent_runs"] = [run_dict(r) for r in info.recent_runs]
    return d


def check_settings_dict(check: CheckSettings) -> dict:
    d = asdict(check)
    d["changed"] = check.changed
    return d
