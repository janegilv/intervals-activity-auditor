import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from config import load_settings
from intervals_api import IntervalsClient
from cache import ActivityCache
from checks.duplicate_overlap import DuplicateOverlapCheck
from checks.under_time_limit import UnderTimeLimitCheck
from checks.over_time_limit import OverTimeLimitCheck
from checks.garmin_link_only import GarminLinkOnlyCheck
from reporting import json_report, csv_report, markdown_report

CHECKS = {
    "duplicate_overlap": DuplicateOverlapCheck,
    "under_time_limit": UnderTimeLimitCheck,
    "over_time_limit": OverTimeLimitCheck,
    "garmin_link_only": GarminLinkOnlyCheck,
}

def parse_args():
    p = argparse.ArgumentParser(description="Audit Intervals.icu activities using pluggable checks.")
    p.add_argument("--offline", action="store_true", help="Use cached activities only.")
    p.add_argument("--full-sync", action="store_true",
                    help="Refresh all activities from the API. Also removes cached activities no "
                         "longer present in Intervals.icu (deleted upstream since the last full sync).")
    p.add_argument("--refresh-days", type=int, help="Override incremental refresh window.")
    p.add_argument("--check", action="append", choices=CHECKS, help="Run a check even if disabled; can be repeated.")
    p.add_argument("--list-checks", action="store_true", help="List checks and exit.")
    p.add_argument(
        "--enrich-strava", action="store_true",
        help="Fetch full activity data from Strava for cached Strava-only stub activities "
             "(requires STRAVA_CLIENT_ID/SECRET/REFRESH_TOKEN in .env; see strava_auth_setup.py). "
             "Already-enriched activities are never re-fetched, so repeat runs are rate-limit-safe.",
    )
    p.add_argument(
        "--strava-auto-retry", action="store_true",
        help="With --enrich-strava: if Strava rate limits you, wait out its Retry-After "
             "(repeating for as long as it keeps happening) and finish this run instead of "
             "stopping. Off by default -- a rate limit stops the run and picks up where it "
             "left off next time.",
    )
    p.add_argument(
        "--strava-debug", type=int, choices=[1, 2], metavar="LEVEL",
        help="With --enrich-strava: print timestamped debug logging. 1 = one summary line "
             "per activity (name/type/start/durations/distance) -- useful for catching bad "
             "data (e.g. an implausible duration) at fetch time instead of only noticing it "
             "later in a check's output. 2 = also dump the full raw Strava payload per "
             "activity (a lot of output; redirect stdout to a file). Omit for no debug "
             "output (default).",
    )
    p.add_argument(
        "--export-strava-cache", metavar="PATH",
        help="Write every resolved Strava record (enriched or confirmed not-found) to PATH as "
             "JSON, then exit without syncing or running checks. Run this before deleting "
             "intervals.sqlite so --import-strava-cache can restore it afterwards instead of "
             "re-fetching everything from Strava.",
    )
    p.add_argument(
        "--import-strava-cache", metavar="PATH",
        help="Re-merge a JSON file created by --export-strava-cache into the current cache "
             "(typically a freshly recreated intervals.sqlite), then exit without syncing or "
             "running checks.",
    )
    return p.parse_args()

def main():
    args = parse_args()
    settings = load_settings()

    if args.list_checks:
        print("AVAILABLE CHECKS\n")
        for name, cls in CHECKS.items():
            cfg = settings.checks.get(name, {})
            print(f"{'✓' if cfg.get('enabled', False) else '○'} {name}")
            print(f"  {'ACTIVE' if cfg.get('enabled', False) else 'DISABLED'}")
            print(f"  {cls.description}\n")
        return

    if args.export_strava_cache:
        from strava_enrich import export_strava_cache
        cache = ActivityCache(settings.cache_db)
        try:
            count = export_strava_cache(cache, args.export_strava_cache)
            print(f"Exported {count} resolved Strava record(s) to {args.export_strava_cache}")
        finally:
            cache.close()
        return

    if args.import_strava_cache:
        from strava_enrich import import_strava_cache
        cache = ActivityCache(settings.cache_db)
        try:
            count = import_strava_cache(cache, args.import_strava_cache)
            print(f"Imported {count} Strava record(s) from {args.import_strava_cache} into {settings.cache_db}")
        finally:
            cache.close()
        return

    cache = ActivityCache(settings.cache_db)
    try:
        if not args.offline:
            if args.full_sync:
                oldest = date(2000, 1, 1)
            else:
                days = args.refresh_days or settings.refresh_days
                oldest = date.today() - timedelta(days=days)
            newest = date.today() + timedelta(days=1)
            client = IntervalsClient(settings.api_key, settings.athlete_id, settings.timeout_seconds)
            payloads = client.list_activities(oldest, newest)
            cache.upsert_many(payloads)
            if args.full_sync:
                # A full sync's response is the complete activity history, so
                # anything cached that isn't in it was deleted upstream.
                # Never do this for an incremental sync -- that only covers a
                # recent window and would wrongly prune everything older.
                current_ids = {str(p.get("id")) for p in payloads}
                removed = cache.prune_missing(current_ids)
                if removed:
                    print(f"Removed {removed} cached activity(ies) no longer present in Intervals.icu.")
            cache.set_state("last_successful_sync", datetime.now(timezone.utc).isoformat())
            print(f"Synced {len(payloads)} activities from {oldest} to {newest}.")
        else:
            print("Offline mode: using cache only.")

        if args.enrich_strava:
            from strava_api import StravaClient
            from strava_enrich import enrich_strava_stubs

            if not (settings.strava_client_id and settings.strava_client_secret and settings.strava_refresh_token):
                raise RuntimeError(
                    "Missing STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET/STRAVA_REFRESH_TOKEN in .env. "
                    "Run strava_auth_setup.py to set them up."
                )
            strava_client = StravaClient(
                settings.strava_client_id, settings.strava_client_secret,
                settings.strava_refresh_token, settings.timeout_seconds,
            )
            strava_result = enrich_strava_stubs(
                cache, strava_client,
                auto_retry_rate_limit=args.strava_auto_retry, debug=args.strava_debug or 0,
            )
            print(
                f"Strava enrichment: {len(strava_result['enriched'])} fetched, "
                f"{strava_result['already_cached']} already cached (skipped), "
                f"{len(strava_result['not_found'])} not found on Strava, "
                f"{len(strava_result['errors'])} errors."
            )
            for activity_id, message in strava_result["errors"].items():
                print(f"  {activity_id}: {message}")
            if strava_result["rate_limited"] and not args.strava_auto_retry:
                print(
                    "\nStopped early due to Strava's rate limit. Run again later to pick up where "
                    "this left off, or re-run now with --strava-auto-retry to wait it out and "
                    "finish automatically instead of stopping."
                )

        activities = cache.all_activities()
        enabled = args.check or [name for name, cfg in settings.checks.items() if cfg.get("enabled", False)]
        findings = []
        for name in enabled:
            cfg = settings.checks.get(name, {})
            check = CHECKS[name](cfg)
            result = check.run(activities)
            findings.extend(result)
            print(f"{name}: {len(result)} findings")

        findings.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2, "info": 3}.get(x["severity"], 9), x["check"]))
        out = Path("output")
        out.mkdir(exist_ok=True)
        json_report.write(findings, out / "findings.json")
        csv_report.write(findings, out / "findings.csv")
        markdown_report.write(findings, out / "findings.md")
        print(f"\nWrote {len(findings)} findings to output/")
    finally:
        cache.close()

if __name__ == "__main__":
    main()
