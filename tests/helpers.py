"""Test helpers: build activity payloads shaped like Intervals.icu's."""
from datetime import datetime, timedelta, timezone

from intervals_auditor.db import Database
from intervals_auditor.repositories import (
    AcknowledgmentRepository, ActivityRepository, IssueRepository, RunRepository, StateRepository,
)


def payload(activity_id, start="2026-09-20T07:00:00", seconds=3600, distance=30000.0,
            type_="Ride", name=None, **extra):
    """An Intervals.icu-style activity payload. `start` is local time; the
    UTC start is two hours earlier (Europe/Oslo in summer)."""
    local = datetime.fromisoformat(start)
    utc = (local - timedelta(hours=2)).replace(tzinfo=timezone.utc)
    p = {
        "id": activity_id,
        "name": name or f"Activity {activity_id}",
        "type": type_,
        "start_date_local": local.isoformat(),
        "start_date": utc.isoformat().replace("+00:00", "Z"),
        "moving_time": seconds,
        "elapsed_time": seconds,
        "distance": distance,
        "source": "GARMIN_CONNECT",
        "strava_id": "123",
    }
    p.update(extra)
    return p


class Repos:
    """All repositories over one in-memory database."""

    def __init__(self):
        self.db = Database(":memory:")
        self.activities = ActivityRepository(self.db)
        self.issues = IssueRepository(self.db)
        self.acks = AcknowledgmentRepository(self.db)
        self.state = StateRepository(self.db)
        self.runs = RunRepository(self.db)

    def add(self, *payloads, now="2026-09-20T10:00:00+00:00"):
        with self.db.transaction():
            return self.activities.upsert_many(list(payloads), now)


class FakeClock:
    def __init__(self, start="2026-09-26T10:00:00+00:00"):
        self.now = datetime.fromisoformat(start)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)
