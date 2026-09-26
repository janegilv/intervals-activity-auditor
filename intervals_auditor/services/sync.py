"""Sync activities from Intervals.icu into the database.

Every sync:

1. fetches the recent window (REFRESH_DAYS, 30 by default) in full, which
   picks up new, edited and deleted recent activities; and
2. once the previous full sync is older than FULL_SYNC_INTERVAL_HOURS
   (24 by default), fetches the whole history instead, which also catches
   edits and deletions of old activities.

Activities that are in the database but no longer returned for the range
that was fetched have been deleted in Intervals.icu and are removed. As a
safety net, a sync that would remove a suspiciously large share of the
activities in that range (e.g. because the API returned a truncated
response) removes nothing and logs a warning instead; `resync --force`
overrides that.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from intervals_auditor.db import Database
from intervals_auditor.models import Activity
from intervals_auditor.repositories import ActivityRepository, StateRepository
from intervals_auditor.timeutil import parse_iso, to_iso

log = logging.getLogger(__name__)

HISTORY_START = date(2000, 1, 1)

STATE_LAST_SYNC = "last_successful_sync"
STATE_LAST_FULL_SYNC = "last_full_sync"

# Refuse to prune when more than this many activities AND more than this
# share of the fetched range would disappear at once.
PRUNE_GUARD_MIN_COUNT = 10
PRUNE_GUARD_MAX_FRACTION = 0.5

STRAVA_STUB_MARKER = "_note"
STRAVA_SOURCE = "STRAVA"


def is_unresolved_stub_payload(payload: dict) -> bool:
    """True if a sync payload is still Intervals.icu's permanent placeholder
    for a Strava-only activity it can't fetch itself (source STRAVA plus its
    "_note" marker).

    Such a payload never improves on data we already fetched from Strava,
    so it's skipped for activities we've resolved -- but only while it's
    still the placeholder: once Intervals.icu has real data of its own for
    the activity (e.g. it got linked to a Garmin file), that update wins."""
    return payload.get("source") == STRAVA_SOURCE and STRAVA_STUB_MARKER in payload


@dataclass
class SyncResult:
    full: bool
    oldest: date
    newest: date
    fetched: int = 0
    inserted: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    preserved: int = 0
    removed: list[Activity] = field(default_factory=list)
    prune_skipped: str | None = None

    def as_dict(self) -> dict:
        return {
            "full": self.full, "oldest": self.oldest.isoformat(), "newest": self.newest.isoformat(),
            "fetched": self.fetched, "inserted": len(self.inserted), "changed": len(self.changed),
            "preserved": self.preserved, "removed": [a.id for a in self.removed],
            "prune_skipped": self.prune_skipped,
        }


class SyncService:
    def __init__(self, db: Database, activities: ActivityRepository, state: StateRepository,
                 client, *, refresh_days: int = 30, full_sync_interval_hours: float = 24, clock):
        self.db = db
        self.activities = activities
        self.state = state
        self.client = client
        self.refresh_days = refresh_days
        self.full_sync_interval = timedelta(hours=full_sync_interval_hours)
        self.clock = clock

    def full_sync_due(self, now: datetime) -> bool:
        last = parse_iso(self.state.get(STATE_LAST_FULL_SYNC))
        return last is None or now - last >= self.full_sync_interval

    def sync(self, *, full: bool | None = None, force_prune: bool = False) -> SyncResult:
        now = self.clock()
        today = now.astimezone().date()
        if full is None:
            full = self.full_sync_due(now)
        oldest = HISTORY_START if full else today - timedelta(days=self.refresh_days)
        newest = today + timedelta(days=1)
        result = SyncResult(full=full, oldest=oldest, newest=newest)

        log.info("Syncing %s from Intervals.icu (%s to %s)",
                 "full history" if full else f"last {self.refresh_days} days", oldest, newest)
        payloads = self.client.list_activities(oldest, newest)
        result.fetched = len(payloads)
        log.debug("Intervals.icu returned %d activities", len(payloads))

        resolved = self.activities.resolved_strava_ids()
        to_upsert = [p for p in payloads
                     if not (str(p.get("id")) in resolved and is_unresolved_stub_payload(p))]
        result.preserved = len(payloads) - len(to_upsert)

        with self.db.transaction():
            stats = self.activities.upsert_many(to_upsert, to_iso(now))
            result.inserted, result.changed = stats.inserted, stats.changed

            fetched_ids = {str(p.get("id")) for p in payloads}
            if full:
                in_range = self.activities.all_ids()
            else:
                # One day of margin at the old end: Intervals.icu's date filter
                # and our stored local dates could disagree right at the edge.
                in_range = self.activities.ids_with_local_date_between(oldest + timedelta(days=1), today)
            missing = in_range - fetched_ids
            if missing and not force_prune and self._looks_truncated(len(missing), len(in_range)):
                result.prune_skipped = (
                    f"{len(missing)} of {len(in_range)} stored activities in the synced range were not "
                    f"returned; not removing them in case the response was incomplete. "
                    f"If you really deleted them, run `python main.py resync --force`."
                )
                log.warning("Not removing activities: %s", result.prune_skipped)
            elif missing:
                result.removed = self.activities.delete_many(missing)

            self.state.set(STATE_LAST_SYNC, to_iso(now))
            if full:
                self.state.set(STATE_LAST_FULL_SYNC, to_iso(now))

        for activity_id in result.inserted:
            log.debug("New activity %s", activity_id)
        for activity_id in result.changed:
            log.debug("Changed activity %s", activity_id)
        for activity in result.removed:
            log.info("Removed activity deleted in Intervals.icu: %s %s %s %r", activity.id,
                     activity.local_start, activity.activity_type, activity.name)
        if result.preserved:
            log.debug("Kept %d Strava-enriched activities instead of Intervals.icu's placeholder",
                      result.preserved)
        log.info("Sync done: %d fetched, %d new, %d changed, %d removed",
                 result.fetched, len(result.inserted), len(result.changed), len(result.removed))
        return result

    @staticmethod
    def _looks_truncated(missing: int, in_range: int) -> bool:
        return missing > PRUNE_GUARD_MIN_COUNT and missing > in_range * PRUNE_GUARD_MAX_FRACTION
