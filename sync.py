"""Sync orchestration: pulling activity data from Intervals.icu into the
local cache.

Kept separate from main.py so the CLI entrypoint stays focused on argument
parsing and presenting results, not on what "syncing" actually means --
the API call, the merge-vs-overwrite decision per activity, and the
prune-on-full-sync behavior are all domain logic, not CLI logic.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone

from strava_enrich import is_unresolved_stub_payload, resolved_strava_ids


@dataclass
class SyncResult:
    oldest: date
    newest: date
    full: bool
    synced_count: int
    preserved_count: int
    removed_count: int  # always 0 for an incremental (non-full) sync


def sync_activities(cache, client, *, oldest: date, newest: date, full: bool) -> SyncResult:
    """Fetch activities from Intervals.icu for [oldest, newest] and merge
    them into cache. Returns a SyncResult describing what happened; callers
    (main.py) are responsible for presenting that to the user -- this
    function has no print()s of its own, so it's usable/testable without
    capturing stdout.

    Two pieces of domain logic live here rather than being obvious from the
    client/cache alone:

    - Preserving already-resolved Strava enrichment: Intervals.icu always
      returns the same permanent placeholder for a Strava-only-linked
      activity it can't fetch full data for itself, so a sync response
      that's STILL that placeholder for an id we've already resolved via
      --enrich-strava is never an improvement, only a downgrade. But
      "already resolved" doesn't mean every future sync for that id should
      be ignored -- Intervals.icu can start sending real data of its own
      later (e.g. the activity gets linked to a matching Garmin file), and
      that update should win over our Strava-fetched copy. So only the
      specific no-op case -- already resolved AND still just the
      placeholder -- is skipped here, not every sync touching that id.

    - Pruning deleted activities: only done when full=True. A full sync's
      response is the complete activity history, so anything cached that
      isn't in it was deleted upstream. Doing this for an incremental sync
      (which only covers a recent window) would wrongly prune everything
      older than that window, so callers must not pass full=True for one.
    """
    payloads = client.list_activities(oldest, newest)

    already_resolved = resolved_strava_ids(cache)
    to_upsert = [
        p for p in payloads
        if str(p.get("id")) not in already_resolved or not is_unresolved_stub_payload(p)
    ]
    preserved_count = len(payloads) - len(to_upsert)
    cache.upsert_many(to_upsert)

    removed_count = 0
    if full:
        current_ids = {str(p.get("id")) for p in payloads}
        removed_count = cache.prune_missing(current_ids)

    cache.set_state("last_successful_sync", datetime.now(timezone.utc).isoformat())

    return SyncResult(
        oldest=oldest,
        newest=newest,
        full=full,
        synced_count=len(payloads),
        preserved_count=preserved_count,
        removed_count=removed_count,
    )
