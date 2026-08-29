import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from config import load_settings
from intervals_api import IntervalsClient
from cache import ActivityCache
from checks.duplicate_overlap import DuplicateOverlapCheck
from checks.under_time_limit import UnderTimeLimitCheck
from checks.garmin_link_only import GarminLinkOnlyCheck
from reporting import json_report, csv_report, markdown_report

CHECKS = {
    "duplicate_overlap": DuplicateOverlapCheck,
    "under_time_limit": UnderTimeLimitCheck,
    "garmin_link_only": GarminLinkOnlyCheck,
}

def parse_args():
    p = argparse.ArgumentParser(description="Audit Intervals.icu activities using pluggable checks.")
    p.add_argument("--offline", action="store_true", help="Use cached activities only.")
    p.add_argument("--full-sync", action="store_true", help="Refresh all activities from the API.")
    p.add_argument("--refresh-days", type=int, help="Override incremental refresh window.")
    p.add_argument("--check", action="append", choices=CHECKS, help="Run a check even if disabled; can be repeated.")
    p.add_argument("--list-checks", action="store_true", help="List checks and exit.")
    p.add_argument(
        "--enrich-strava", action="store_true",
        help="Fetch full activity data from Strava for cached Strava-only stub activities "
             "(requires STRAVA_CLIENT_ID/SECRET/REFRESH_TOKEN in .env; see strava_auth_setup.py). "
             "Already-enriched activities are never re-fetched, so repeat runs are rate-limit-safe.",
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
            result = enrich_strava_stubs(cache, strava_client)
            print(
                f"Strava enrichment: {len(result['enriched'])} fetched, "
                f"{result['already_cached']} already cached (skipped), "
                f"{len(result['not_found'])} not found on Strava, "
                f"{len(result['errors'])} errors."
            )
            for activity_id, message in result["errors"].items():
                print(f"  {activity_id}: {message}")

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
