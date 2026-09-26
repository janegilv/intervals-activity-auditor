"""Fetch activity streams from Intervals.icu for the checks that need them.

Only activities of the types a check asks for (see
ActivityCheck.stream_activity_types) are fetched, and only those that have
GPS data and aren't indoor/trainer sessions. Each one is fetched once and
again only when the activity changes (streams.version_of). Newest first,
and at most STREAMS_PER_RUN per run, so the first fill of a long history
is spread over several runs -- or done at once with `fetch-streams`.
"""
import logging
from dataclasses import dataclass, field

from intervals_auditor.db import Database
from intervals_auditor.models import Activity
from intervals_auditor.repositories import ActivityRepository, StreamRepository
from intervals_auditor.sources.intervals import IntervalsRateLimitError
from intervals_auditor.streams import parse_api, stream_types_for, version_of
from intervals_auditor.timeutil import to_iso

log = logging.getLogger(__name__)

# Give up for this run after this many failed fetches in a row (network down).
MAX_CONSECUTIVE_ERRORS = 3


@dataclass
class StreamsResult:
    fetched: int = 0
    unavailable: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    remaining: int = 0
    rate_limited: bool = False

    def as_dict(self) -> dict:
        return {"fetched": self.fetched, "unavailable": self.unavailable, "errors": len(self.errors),
                "remaining": self.remaining, "rate_limited": self.rate_limited}


def wants_streams(activity: Activity, types: set[str]) -> bool:
    if activity.activity_type not in types or activity.is_trainer or activity.is_indoor:
        return False
    available = activity.raw.get("stream_types") or []
    return "time" in available and "distance" in available


class StreamService:
    def __init__(self, db: Database, activities: ActivityRepository, streams: StreamRepository, client, *, clock):
        self.db = db
        self.activities = activities
        self.streams = streams
        self.client = client
        self.clock = clock

    def pending(self, types: set[str]) -> list[Activity]:
        """Activities whose streams are needed but not stored (or stale),
        newest first."""
        records = self.streams.records()
        waiting = []
        for activity in self.activities.all():
            if not wants_streams(activity, types):
                continue
            record = records.get(activity.id)
            if record and record.version == version_of(activity) and record.status != "error":
                continue
            waiting.append(activity)
        waiting.sort(key=lambda a: a.start_date or a.start_date_local or "", reverse=True)
        return waiting

    def fetch(self, types: set[str], limit: int | None = None) -> StreamsResult:
        result = StreamsResult()
        waiting = self.pending(types)
        if not waiting:
            log.debug("Streams: nothing to fetch")
            return result
        todo = waiting[:limit] if limit else waiting
        log.info("Streams: fetching %d of %d activit%s waiting", len(todo), len(waiting),
                 "y" if len(waiting) == 1 else "ies")
        consecutive_errors = 0
        for index, activity in enumerate(todo, 1):
            try:
                payload = self.client.get_streams(activity.id, stream_types_for(activity))
                streams = parse_api(payload) if payload is not None else None
            except IntervalsRateLimitError as e:
                result.rate_limited = True
                log.warning("Streams: Intervals.icu asks us to slow down (%s); the rest waits for the next run", e)
                break
            except Exception as e:  # noqa: BLE001 - one failed fetch shouldn't stop the rest
                result.errors[activity.id] = f"{type(e).__name__}: {e}"
                log.warning("Streams: fetching %s failed: %s", activity.id, result.errors[activity.id])
                with self.db.transaction():
                    self.streams.save(activity.id, version_of(activity), to_iso(self.clock()), None,
                                      status="error", error=result.errors[activity.id])
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    log.warning("Streams: %d failures in a row; trying again next run", consecutive_errors)
                    break
                continue
            consecutive_errors = 0
            with self.db.transaction():
                self.streams.save(activity.id, version_of(activity), to_iso(self.clock()), streams)
            if streams is None:
                result.unavailable += 1
                log.debug("Streams: %s has none", activity.id)
            else:
                result.fetched += 1
                log.debug("Streams: %s %s %r: %d points", activity.id, activity.local_start, activity.name,
                          len(streams))
            if index % 50 == 0:
                log.info("Streams: %d of %d done", index, len(todo))
        result.remaining = len(waiting) - result.fetched - result.unavailable
        log.info("Streams: %d fetched, %d without streams, %d failed, %d still waiting",
                 result.fetched, result.unavailable, len(result.errors), result.remaining)
        return result
