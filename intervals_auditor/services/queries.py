"""Read-only queries that assemble the activity-centric views used by the
CLI, reports and notifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from intervals_auditor.models import Acknowledgment, Activity
from intervals_auditor.repositories import (
    AcknowledgmentRepository, ActivityRepository, IssueRepository, RunRecord, RunRepository, StateRepository,
)
from intervals_auditor.services.sync import STATE_LAST_FULL_SYNC, STATE_LAST_SYNC
from intervals_auditor.timeutil import parse_iso
from intervals_auditor.views import ACKNOWLEDGED, OPEN, ActivityView, IssueView, group_by_activity

STATE_SYNC_FAILING_SINCE = "sync_failing_since"
STATE_SYNC_LAST_ERROR = "sync_last_error"


@dataclass
class ActivityDetail:
    activity_id: str
    activity: Activity | None
    issues: list[IssueView] = field(default_factory=list)
    acks: list[Acknowledgment] = field(default_factory=list)


@dataclass
class StatusInfo:
    activity_count: int
    open_issues: int
    acknowledged_issues: int
    resolved_issues: int
    active_acks: int
    last_sync: str | None
    last_full_sync: str | None
    sync_failing_since: str | None
    sync_last_error: str | None
    recent_runs: list[RunRecord]


class QueryService:
    def __init__(self, activities: ActivityRepository, issues: IssueRepository,
                 acks: AcknowledgmentRepository, state: StateRepository, runs: RunRepository):
        self.activities = activities
        self.issues = issues
        self.acks = acks
        self.state = state
        self.runs = runs

    def _issue_views(self, *, checks=None, activity_id=None) -> list[IssueView]:
        active = self.acks.active_by_issue_key()
        records = self.issues.list(checks=checks, activity_id=activity_id)
        return [IssueView.from_record(r, active.get(r.key)) for r in records]

    def activity_views(self, *, states=(OPEN,), checks=None) -> list[ActivityView]:
        """Activities with issues in the given states, newest first."""
        views = [v for v in self._issue_views(checks=checks) if v.state in states]
        return group_by_activity(views, self.activities.get_many({v.activity_id for v in views}))

    def report_views(self) -> list[ActivityView]:
        return self.activity_views(states=(OPEN, ACKNOWLEDGED))

    def _known_activity(self, activity_id: str) -> bool:
        return bool(self.activities.get(activity_id)
                    or self.issues.list(activity_id=activity_id)
                    or self.acks.list(include_revoked=True, activity_id=activity_id))

    def resolve_activity_id(self, ref: str) -> str | None:
        """The stored activity id for what was typed: exactly as stored, or
        without Intervals.icu's "i" prefix (179138245 finds i179138245).
        Activities that have since been deleted are found through their
        issues/acknowledgments."""
        ref = ref.strip()
        candidates = [ref]
        if ref.isdigit():
            candidates.append(f"i{ref}")
        elif ref[:1] == "I" and ref[1:].isdigit():
            candidates.append(f"i{ref[1:]}")
        return next((c for c in candidates if self._known_activity(c)), None)

    def activity_detail(self, activity_id: str) -> ActivityDetail | None:
        activity = self.activities.get(activity_id)
        issues = self._issue_views(activity_id=activity_id)
        acks = self.acks.list(include_revoked=True, activity_id=activity_id)
        if activity is None and not issues and not acks:
            return None
        issues.sort(key=lambda v: ({"open": 0, "acknowledged": 1, "resolved": 2}[v.state], v.check, v.key))
        return ActivityDetail(activity_id, activity, issues, acks)

    def notifiable(self, cutoff: datetime) -> tuple[list[ActivityView], int]:
        """Open, unacknowledged issues not notified about yet, split into
        those old enough to notify (grouped by activity) and the number
        still inside the waiting period."""
        ready, waiting = [], 0
        for view in self._issue_views():
            if view.state != OPEN or view.notified_at:
                continue
            first_seen = parse_iso(view.first_seen_at)
            if first_seen is not None and first_seen <= cutoff:
                ready.append(view)
            else:
                waiting += 1
        return group_by_activity(ready, self.activities.get_many({v.activity_id for v in ready})), waiting

    def status(self) -> StatusInfo:
        views = self._issue_views()
        return StatusInfo(
            activity_count=self.activities.count(),
            open_issues=sum(1 for v in views if v.state == OPEN),
            acknowledged_issues=sum(1 for v in views if v.state == ACKNOWLEDGED),
            resolved_issues=sum(1 for v in views if v.state not in (OPEN, ACKNOWLEDGED)),
            active_acks=len(self.acks.list()),
            last_sync=self.state.get(STATE_LAST_SYNC),
            last_full_sync=self.state.get(STATE_LAST_FULL_SYNC),
            sync_failing_since=self.state.get(STATE_SYNC_FAILING_SINCE),
            sync_last_error=self.state.get(STATE_SYNC_LAST_ERROR),
            recent_runs=self.runs.recent(5),
        )
