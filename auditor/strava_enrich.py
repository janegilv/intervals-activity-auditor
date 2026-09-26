import json
import time
from datetime import datetime, timezone
from pathlib import Path

from auditor.sources.strava import StravaRateLimitError

STUB_MARKER = "_note"  # present only on the minimal stub Intervals.icu returns
STUB_SOURCE = "STRAVA"

# Used only when Strava's 429 response doesn't include a Retry-After header.
# Strava's short-window rate limit resets every 15 minutes, so that's the
# safest generic guess; the daily limit resetting would need much longer,
# but Strava has no way to tell us which one was hit if it omits the header.
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 900


def _is_unenriched_stub(activity) -> bool:
    """True for a placeholder row Intervals.icu created (source == STRAVA)
    that we have never resolved yet, one way or the other. STUB_MARKER is
    Intervals.icu's own note on an un-fetched stub; we remove it ourselves
    once we've recorded a result -- successful enrichment OR a confirmed
    404 -- so neither kind is ever looked up again. Only a genuinely
    transient error (network blip, rate limit) leaves the marker in place,
    so those are retried on the next run."""
    return activity.raw.get("source") == STUB_SOURCE and STUB_MARKER in activity.raw


def resolved_strava_ids(cache) -> set:
    """IDs of Strava-sourced activities already resolved (enriched, or
    confirmed not-found) by a previous --enrich-strava run.

    Intervals.icu's API always returns the same minimal placeholder for a
    Strava-only-linked activity it can't fetch full data for -- that's
    permanent, not "not yet available". So a sync response that is still
    that exact placeholder is never an improvement over what we already
    resolved, and handing it to cache.upsert_many would silently overwrite
    good data with it. main.py's sync step uses this together with
    is_unresolved_stub_payload() below to filter out only that specific
    no-op case -- not every future sync for this id (see that function's
    docstring for why the distinction matters)."""
    return {
        a.id for a in cache.all_activities()
        if a.raw.get("source") == STUB_SOURCE and "_strava_lookup" in a.raw
    }


def is_unresolved_stub_payload(payload: dict) -> bool:
    """True if a sync payload is still Intervals.icu's permanent placeholder
    shape (source == STRAVA, carries STUB_MARKER) for an activity it can't
    fetch full data for itself.

    This is deliberately narrower than "this id was already resolved via
    Strava". An activity can start as a Strava-only stub, get resolved by
    --enrich-strava, and later gain real data on Intervals.icu's own side --
    e.g. it gets linked to a matching Garmin file, or Intervals.icu's
    integration simply catches up. When that happens the sync payload for
    that id is no longer this placeholder shape, and it should be let
    through to overwrite our Strava-fetched copy: Intervals.icu having its
    own real data for the activity is strictly better than what we
    backfilled from Strava. Only an id that is both already resolved AND
    still sending this exact placeholder should be skipped -- that
    combination is the only one that can't possibly be an improvement."""
    return payload.get("source") == STUB_SOURCE and STUB_MARKER in payload


def _fetch_with_optional_retry(client, activity_id, auto_retry_rate_limit, on_wait=None):
    """Calls client.get_activity(activity_id). On a 429, either waits out
    Strava's own Retry-After (or DEFAULT_RATE_LIMIT_WAIT_SECONDS if Strava
    didn't send one) and tries again -- repeating for as long as Strava keeps
    rate limiting -- or, if auto_retry_rate_limit is False, re-raises
    immediately so the caller can stop the run."""
    while True:
        try:
            return client.get_activity(activity_id)
        except StravaRateLimitError as e:
            if not auto_retry_rate_limit:
                raise
            wait_seconds = e.retry_after or DEFAULT_RATE_LIMIT_WAIT_SECONDS
            if on_wait:
                on_wait(activity_id, wait_seconds)
            time.sleep(wait_seconds)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _summarize(payload: dict) -> str:
    """One line covering what usually matters for spotting bad data at a
    glance -- name, type, start, and every duration/distance field -- without
    dumping the whole payload."""
    return (
        f"name={payload.get('name')!r} type={(payload.get('type') or payload.get('sport_type'))!r} "
        f"start={payload.get('start_date') or payload.get('start_date_local')} "
        f"moving_time={payload.get('moving_time')} elapsed_time={payload.get('elapsed_time')} "
        f"distance={payload.get('distance')}"
    )


def enrich_strava_stubs(cache, client, activities=None, auto_retry_rate_limit=False, debug=0) -> dict:
    """Find cached Strava-stub activities with no recorded result yet, look
    each one up on Strava, and persist the outcome back into the same sqlite
    row via cache.upsert_many.

    A successful fetch fills in real name/type/duration/etc. A confirmed
    404 (Strava has no such activity -- e.g. deleted, or from a
    disconnected/older Strava account) is recorded too, with no invented
    data, purely so it's never queried again. Either way, the id is removed
    from consideration on every subsequent run without another API call --
    that's what keeps repeat runs rate-limit-safe.

    auto_retry_rate_limit=False (the default): the first 429 stops the run
    right there. Nothing already resolved is lost -- each activity is
    committed to sqlite as soon as it's fetched -- and the remaining stubs
    are simply picked up next time you run this.

    auto_retry_rate_limit=True: instead of stopping, wait out Strava's
    Retry-After (repeating for as long as Strava keeps saying 429) and keep
    going until every stub in this run is resolved. This can mean a long,
    unattended wait if you've hit Strava's daily limit rather than the
    15-minute one, since Strava doesn't say which one you hit.

    debug: 0 (default) is silent. 1 prints one timestamped summary line per
    activity (name/type/start/durations/distance) -- this is how the
    631-day-duration data issue would have been visible at fetch time
    instead of only showing up later in a check's output. 2 additionally
    dumps the full raw Strava payload per activity, which is a lot of
    output; redirect stdout to a file for a long run at that level.

    Returns a summary dict: {"enriched": [...ids], "not_found": [...ids],
    "already_cached": count, "errors": {id: message}, "rate_limited": bool}.
    """
    activities = activities if activities is not None else cache.all_activities()
    stubs = [a for a in activities if _is_unenriched_stub(a)]
    already_cached = len(activities) - len(stubs)

    result = {
        "enriched": [], "not_found": [], "errors": {},
        "already_cached": already_cached, "rate_limited": False,
    }

    def log(message, level=1):
        if debug >= level:
            print(f"[{_timestamp()}] [strava-debug] {message}")

    def on_wait(activity_id, wait_seconds):
        prefix = f"[{_timestamp()}] " if debug else ""
        print(f"{prefix}  [strava] rate limited while fetching {activity_id}; waiting {wait_seconds}s before retrying...")

    for stub in stubs:
        # Only at level 2 -- at level 1 this would just be noise ahead of the
        # one line per activity that level 1 is meant to be.
        log(f"fetching {stub.id} (cached stub start_date_local={stub.start_date_local!r})", level=2)
        try:
            payload = _fetch_with_optional_retry(client, stub.id, auto_retry_rate_limit, on_wait=on_wait)
        except StravaRateLimitError:
            # Only reachable when auto_retry_rate_limit is False. Stop here,
            # not mid-write -- everything resolved so far is already
            # committed to sqlite (one upsert per activity, below), so the
            # remaining stubs simply get picked up on the next run.
            log(f"{stub.id}: rate limited, stopping this run")
            result["rate_limited"] = True
            result["errors"][stub.id] = (
                "rate limited by Strava; stopping without retrying (already-resolved activities "
                "were kept). Re-run with --strava-auto-retry to wait out the rate limit and finish "
                "this run automatically instead."
            )
            break
        except Exception as e:  # noqa: BLE001 - a transient failure for this one id shouldn't abort the batch
            log(f"{stub.id}: error - {e}")
            result["errors"][stub.id] = str(e)
            continue

        if payload is None:
            log(f"{stub.id}: not found on Strava (404)")
            # Confirmed absent on Strava -- record that so it's never re-queried,
            # without inventing any name/type/duration data for it.
            not_found_record = dict(stub.raw)
            not_found_record["id"] = stub.id
            not_found_record["source"] = STUB_SOURCE
            not_found_record.pop(STUB_MARKER, None)
            not_found_record["_strava_lookup"] = "not_found"
            not_found_record["_enriched_at"] = datetime.now(timezone.utc).isoformat()
            cache.upsert_many([not_found_record])
            result["not_found"].append(stub.id)
            continue

        # Level 1: exactly one line, built from what Strava actually sent back
        # (before any of our own merging/normalizing).
        log(f"{stub.id}: enriched - {_summarize(payload)}")
        # Level 2: everything, verbatim.
        log(f"{stub.id}: full payload:\n{json.dumps(payload, indent=2, default=str)}", level=2)

        merged = dict(payload)
        merged["id"] = stub.id
        merged["source"] = STUB_SOURCE
        merged["type"] = payload.get("type") or payload.get("sport_type") or ""
        merged["_strava_lookup"] = "enriched"
        merged["_enriched_at"] = datetime.now(timezone.utc).isoformat()
        merged.pop(STUB_MARKER, None)

        cache.upsert_many([merged])
        result["enriched"].append(stub.id)

    return result


def export_strava_cache(cache, path) -> int:
    """Snapshot every resolved Strava record (successfully enriched, or a
    confirmed not-found) to a JSON file, independent of the sqlite cache.

    This is the safety net for the "delete intervals.sqlite to force a clean
    resync" workflow: do that as before, but export first and import after,
    and the Strava side of the cache survives the wipe intact -- no need to
    walk back through Strava's rate limit just to get back where you were.

    Unresolved stubs aren't included; there's nothing to preserve for those,
    they'll simply be looked up again normally after the cache is rebuilt.
    """
    activities = cache.all_activities()
    resolved = [
        a.raw for a in activities
        if a.raw.get("source") == STUB_SOURCE and "_strava_lookup" in a.raw
    ]
    Path(path).write_text(json.dumps(resolved, indent=2), encoding="utf-8")
    return len(resolved)


def import_strava_cache(cache, path) -> int:
    """Re-merge a JSON file created by export_strava_cache back into the
    current sqlite cache (typically a freshly recreated one) through the
    normal upsert path, so those activities are immediately recognized as
    already resolved and are never re-fetched from Strava."""
    payloads = json.loads(Path(path).read_text(encoding="utf-8"))
    if payloads:
        cache.upsert_many(payloads)
    return len(payloads)
