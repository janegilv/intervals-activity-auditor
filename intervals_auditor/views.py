"""Read models: activities together with their issues (and acknowledgments),
ready for the CLI, reports, notifications or a future web page to render.
Nothing here touches the database -- see services/queries.py for that."""
from __future__ import annotations

from dataclasses import dataclass, field

from intervals_auditor.models import SEVERITY_RANK, Acknowledgment, Activity, Issue, IssueRecord, activity_url

OPEN = "open"
ACKNOWLEDGED = "acknowledged"
RESOLVED = "resolved"


@dataclass
class IssueView:
    key: str
    activity_id: str
    check: str
    severity: str
    summary: str
    related_activity_ids: tuple[str, ...]
    details: list[dict]
    state: str
    first_seen_at: str | None = None
    resolved_at: str | None = None
    notified_at: str | None = None
    reopen_reason: str | None = None
    ack: Acknowledgment | None = None
    number: int | None = None

    @classmethod
    def from_issue(cls, issue: Issue) -> IssueView:
        return cls(issue.key, issue.activity_id, issue.check, issue.severity, issue.summary,
                   issue.related_activity_ids, issue.details, OPEN)

    @classmethod
    def from_record(cls, record: IssueRecord, ack: Acknowledgment | None = None) -> IssueView:
        if not record.is_open:
            state = RESOLVED
        elif ack is not None:
            state = ACKNOWLEDGED
        else:
            state = OPEN
        return cls(record.key, record.activity_id, record.check, record.severity, record.summary,
                   record.related_activity_ids, record.details, state,
                   first_seen_at=record.first_seen_at, resolved_at=record.resolved_at,
                   notified_at=record.notified_at, reopen_reason=record.reopen_reason, ack=ack,
                   number=record.number)

    @property
    def related_urls(self) -> list[tuple[str, str]]:
        return [(rid, activity_url(rid)) for rid in self.related_activity_ids]


@dataclass
class ActivityView:
    activity_id: str
    activity: Activity | None  # None if the activity has since been deleted
    issues: list[IssueView] = field(default_factory=list)

    @property
    def url(self) -> str:
        return activity_url(self.activity_id)

    @property
    def worst_severity(self) -> str:
        return min((i.severity for i in self.issues), key=lambda s: SEVERITY_RANK.get(s, 99), default="info")

    def issues_in(self, *states: str) -> list[IssueView]:
        return [i for i in self.issues if i.state in states]


def _issue_sort_key(issue: IssueView):
    number = issue.number if issue.number is not None else float("inf")
    return (SEVERITY_RANK.get(issue.severity, 99), issue.check, number, issue.key)


def group_by_activity(issues: list[IssueView], activities: dict[str, Activity]) -> list[ActivityView]:
    """Group issues under their activity, newest activity first."""
    views: dict[str, ActivityView] = {}
    for issue in issues:
        view = views.setdefault(issue.activity_id, ActivityView(issue.activity_id, activities.get(issue.activity_id)))
        view.issues.append(issue)
    for view in views.values():
        view.issues.sort(key=_issue_sort_key)

    def newest_first(view: ActivityView):
        start = view.activity.start.isoformat() if view.activity and view.activity.start else ""
        return start, view.activity_id

    return sorted(views.values(), key=newest_first, reverse=True)
