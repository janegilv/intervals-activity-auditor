"""Domain model. The application is activity-centric: an Activity is the
subject, an Issue is a problem a check found on one activity, and an
Acknowledgment says "I've looked at this issue on this activity, it's fine".

Checks that compare activities with each other (e.g. duplicate_overlap)
still produce issues per activity: each activity involved gets its own
Issue that names the other activities in related_activity_ids.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

SEVERITIES = ("high", "medium", "low", "info")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}

INTERVALS_ACTIVITY_URL = "https://intervals.icu/activities/{id}"


def activity_url(activity_id: str) -> str:
    return INTERVALS_ACTIVITY_URL.format(id=activity_id)


@dataclass
class Activity:
    id: str
    name: str
    activity_type: str
    start_date_local: str | None
    start_date: str | None
    moving_time: int | None
    elapsed_time: int | None
    distance: float | None
    raw: dict[str, Any]
    synced_at: str | None = None
    fingerprint: str | None = None
    first_seen_at: str | None = None
    updated_at: str | None = None

    @property
    def duration_seconds(self) -> int:
        return int(self.elapsed_time or self.moving_time or 0)

    @property
    def start(self) -> datetime | None:
        value = self.start_date or self.start_date_local
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        # start_date_local (used as a fallback when start_date/UTC is missing,
        # e.g. some manually-logged activities) has no offset, which produces a
        # timezone-naive datetime. Comparing that against the timezone-aware
        # datetimes parsed from start_date crashes (TypeError) as soon as both
        # kinds end up sorted/compared together, e.g. in duplicate_overlap. Treat
        # a naive value as UTC so every Activity.start is always comparable.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @property
    def end(self) -> datetime | None:
        start = self.start
        return start + timedelta(seconds=self.duration_seconds) if start else None

    @property
    def url(self) -> str:
        return activity_url(self.id)

    @property
    def local_start(self) -> str:
        """Start time as the athlete saw it on their watch, "YYYY-MM-DD HH:MM"."""
        value = self.start_date_local or self.start_date or ""
        return value[:16].replace("T", " ") if value else "-"

    @property
    def is_trainer(self) -> bool:
        """True for an indoor trainer session (Garmin/Intervals.icu's own
        "trainer" flag on the activity). These legitimately have no GPS
        distance regardless of activity_type -- e.g. a "Ride" done on a
        smart trainer -- so checks that reason about outdoor GPS data (like
        missing_distance) must exclude these rather than relying on type
        alone."""
        return bool(self.raw.get("trainer"))

    @property
    def is_indoor(self) -> bool:
        """True for an indoor session flagged via Intervals.icu's separate
        "indoor" field (e.g. a treadmill run), distinct from is_trainer
        which covers Garmin's own trainer flag (seen so far only on rides
        in this account). Not observed set in practice here, but kept as
        its own check since the two fields aren't guaranteed to overlap for
        every source/device combination."""
        return bool(self.raw.get("indoor"))

    def snapshot(self) -> dict[str, Any]:
        """The facts worth keeping about an activity when it's acknowledged,
        so it can still be investigated later even if it's since been
        changed or deleted."""
        return {
            "id": self.id,
            "url": self.url,
            "name": self.name,
            "type": self.activity_type,
            "start_local": self.start_date_local,
            "duration_seconds": self.duration_seconds,
            "distance": self.distance,
            "source": self.raw.get("source"),
            "fingerprint": self.fingerprint,
        }


def issue_key(activity_id: str, check: str, related_activity_ids=()) -> str:
    """Stable identity of an issue: which activity, which check, and (for
    checks comparing activities) which other activities it involves."""
    key = f"{activity_id}|{check}"
    if related_activity_ids:
        key += "|" + "+".join(sorted(related_activity_ids))
    return key


@dataclass
class Issue:
    """A problem one check found on one activity, as produced by a check run."""
    activity_id: str
    check: str
    severity: str
    summary: str
    related_activity_ids: tuple[str, ...] = ()
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> str:
        return issue_key(self.activity_id, self.check, self.related_activity_ids)


@dataclass
class IssueRecord:
    """An issue as tracked over time in the database.

    status is "open" while the latest check run still finds it and
    "resolved" once it doesn't (the activity was fixed or deleted). An
    open issue can additionally be acknowledged -- that's stored separately
    as an Acknowledgment, see IssueView for the combined state.
    """
    key: str
    activity_id: str
    check: str
    related_activity_ids: tuple[str, ...]
    severity: str
    summary: str
    details: list[dict[str, Any]]
    status: str
    first_seen_at: str
    last_seen_at: str
    resolved_at: str | None = None
    notified_at: str | None = None
    open_count: int = 1
    reopen_reason: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status == "open"


@dataclass
class Acknowledgment:
    id: int
    issue_key: str
    activity_id: str
    check: str
    related_activity_ids: tuple[str, ...]
    note: str
    fingerprints: dict[str, str | None]
    snapshot: dict[str, dict[str, Any]]
    created_at: str
    revoked_at: str | None = None
    revoked_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @property
    def involved_activity_ids(self) -> tuple[str, ...]:
        return (self.activity_id, *self.related_activity_ids)

    @property
    def url(self) -> str:
        return activity_url(self.activity_id)
