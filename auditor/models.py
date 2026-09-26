from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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
    cached_at: str | None = None

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
        return f"https://intervals.icu/activities/{self.id}"

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

