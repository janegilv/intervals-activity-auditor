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

    @property
    def average_speed_kmh(self) -> float | None:
        """Average speed in km/h. Normally distance (meters) / moving_time
        (seconds) -- the source already pause-filters moving_time in the
        ordinary case, so this is the right denominator.

        One specific situation makes moving_time unreliable though: some
        activities (seen so far only ones synced from a raw GPX import,
        e.g. device_name "StravaGPX") never got a real pause-filtered
        moving_time computed at all -- the source just set it equal to
        elapsed_time, silently swallowing whatever gap was in the original
        recording (e.g. a multi-hour stop with the GPS timer left running).
        That produces a wildly-too-slow average speed with no way to tell
        it apart from a normal, genuinely continuous activity just by
        looking at moving_time/elapsed_time alone.

        icu_recording_time -- the source's own "how much of this window has
        actual data" figure -- catches it: when moving_time equals
        elapsed_time (no real pause-filtering happened) but
        icu_recording_time is far smaller than that, there was a real,
        unaccounted-for gap. In that specific case, trust the source's own
        pre-computed average_speed field (derived from the real underlying
        stream) instead of dividing by the misleading moving_time.

        This is deliberately narrow rather than always preferring the raw
        field: tested against the full cache, always trusting it introduces
        new bad readings elsewhere (e.g. open-water swim GPS data is its
        own separate mess, and an activity with no distance recorded at all
        gets a nonsense speed from an almost-empty raw record) -- so it's
        only used in the one situation it's actually known to fix.
        """
        if not self.distance:
            return None

        if self.moving_time and self.moving_time == self.elapsed_time:
            recording_time = self.raw.get("icu_recording_time")
            if recording_time and self.elapsed_time and recording_time < self.elapsed_time * 0.5:
                raw_speed = self.raw.get("average_speed")
                if raw_speed:
                    return raw_speed * 3.6

        if not self.moving_time:
            return None
        return (self.distance / 1000) / (self.moving_time / 3600)
