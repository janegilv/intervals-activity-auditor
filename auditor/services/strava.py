"""Fill in Strava-only activities from the Strava API.

Some activities come from Intervals.icu as a bare placeholder -- source
STRAVA, a timestamp and a "_note", no name/type/duration -- because
Intervals.icu can't fetch Strava data through its own API. This commonly
happens when the Strava copy was cropped, split or merged independently of
the Garmin-recorded one. Enrichment looks each placeholder up on Strava
once and stores the result, so the checks can work with real data.

Each placeholder is resolved exactly once: a successful fetch stores the
Strava data, and a confirmed 404 is recorded too, so neither is queried
again. If Strava rate-limits us the run stops there; nothing fetched so
far is lost and the rest is picked up by the next run.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from auditor.db import Database
from auditor.logging_setup import TRACE
from auditor.repositories import ActivityRepository
from auditor.sources.strava import StravaRateLimitError
from auditor.timeutil import to_iso

log = logging.getLogger(__name__)

STUB_MARKER = "_note"
STUB_SOURCE = "STRAVA"


@dataclass
class EnrichResult:
    enriched: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    rate_limited: bool = False
    remaining: int = 0

    def as_dict(self) -> dict:
        return {"enriched": len(self.enriched), "not_found": len(self.not_found),
                "errors": len(self.errors), "rate_limited": self.rate_limited,
                "remaining": self.remaining}


def _summarize(payload: dict) -> str:
    return (
        f"name={payload.get('name')!r} type={(payload.get('type') or payload.get('sport_type'))!r} "
        f"start={payload.get('start_date') or payload.get('start_date_local')} "
        f"moving_time={payload.get('moving_time')} elapsed_time={payload.get('elapsed_time')} "
        f"distance={payload.get('distance')}"
    )


def enrich_strava_stubs(db: Database, activities: ActivityRepository, client, *, clock) -> EnrichResult:
    stubs = activities.unresolved_strava_stubs()
    result = EnrichResult()
    if not stubs:
        log.debug("Strava: no placeholders to look up")
        return result
    log.info("Strava: looking up %d placeholder activit%s", len(stubs), "y" if len(stubs) == 1 else "ies")

    for index, stub in enumerate(stubs):
        log.debug("Strava: fetching %s (placeholder start %s)", stub.id, stub.start_date_local)
        try:
            payload = client.get_activity(stub.id)
        except StravaRateLimitError as e:
            result.rate_limited = True
            result.remaining = len(stubs) - index
            log.warning("Strava: rate limited (%s); %d placeholder(s) left for the next run",
                        e, result.remaining)
            break
        except Exception as e:  # noqa: BLE001 - one failed lookup shouldn't stop the rest
            result.errors[stub.id] = str(e)
            log.error("Strava: fetching %s failed: %s", stub.id, e)
            continue

        now = to_iso(clock())
        if payload is None:
            record = dict(stub.raw)
            record.update({"id": stub.id, "source": STUB_SOURCE, "_strava_lookup": "not_found",
                           "_enriched_at": now})
            record.pop(STUB_MARKER, None)
            result.not_found.append(stub.id)
            log.info("Strava: %s not found on Strava (recorded, won't be looked up again)", stub.id)
        else:
            log.debug("Strava: %s enriched - %s", stub.id, _summarize(payload))
            log.log(TRACE, "Strava: %s full payload:\n%s", stub.id, json.dumps(payload, indent=2, default=str))
            record = dict(payload)
            record.update({"id": stub.id, "source": STUB_SOURCE,
                           "type": payload.get("type") or payload.get("sport_type") or "",
                           "_strava_lookup": "enriched", "_enriched_at": now})
            record.pop(STUB_MARKER, None)
            result.enriched.append(stub.id)

        # Commit each one, so a later failure never loses earlier lookups.
        with db.transaction():
            activities.upsert_many([record], now)

    log.info("Strava: %d enriched, %d not found, %d errors%s", len(result.enriched),
             len(result.not_found), len(result.errors), ", stopped by rate limit" if result.rate_limited else "")
    return result


def export_strava_records(activities: ActivityRepository, path: Path) -> int:
    """Write every resolved Strava record to a JSON file (a backup)."""
    records = activities.resolved_strava_records()
    Path(path).write_text(json.dumps(records, indent=2), encoding="utf-8")
    return len(records)


def import_strava_records(db: Database, activities: ActivityRepository, path: Path, *, clock) -> int:
    """Load records written by export_strava_records back into the database."""
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if records:
        with db.transaction():
            activities.upsert_many(records, to_iso(clock()))
    return len(records)
