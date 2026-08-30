from dataclasses import dataclass
from pathlib import Path
import os
import tomllib
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Settings:
    api_key: str
    athlete_id: str
    cache_db: Path
    refresh_days: int
    timeout_seconds: int
    checks: dict
    strava_client_id: str
    strava_client_secret: str
    strava_refresh_token: str

def load_settings():
    api_key = os.getenv("INTERVALS_API_KEY", "").strip()
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID", "").strip()
    if not api_key or not athlete_id:
        raise RuntimeError(
            "Missing INTERVALS_API_KEY or INTERVALS_ATHLETE_ID. Copy .env.example to .env and fill them in."
        )
    with open("checks.toml", "rb") as f:
        checks = tomllib.load(f)

    # Personal, account-specific overrides (e.g. excluding one of your own
    # activities from a check) live in checks.local.toml instead of the
    # tracked checks.toml -- see checks.local.toml.example. Optional: most
    # setups won't have one. Each section here is merged on top of the
    # matching section in checks.toml, one level deep -- a key you don't
    # set locally still falls back to checks.toml's value.
    local_path = Path("checks.local.toml")
    if local_path.exists():
        with open(local_path, "rb") as f:
            local_checks = tomllib.load(f)
        for name, overrides in local_checks.items():
            checks.setdefault(name, {}).update(overrides)

    return Settings(
        api_key=api_key,
        athlete_id=athlete_id,
        cache_db=Path(os.getenv("CACHE_DB", "cache/intervals.sqlite")),
        refresh_days=int(os.getenv("REFRESH_DAYS", "30")),
        timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
        checks=checks,
        # Only required if --enrich-strava is used; left blank otherwise.
        strava_client_id=os.getenv("STRAVA_CLIENT_ID", "").strip(),
        strava_client_secret=os.getenv("STRAVA_CLIENT_SECRET", "").strip(),
        strava_refresh_token=os.getenv("STRAVA_REFRESH_TOKEN", "").strip(),
    )
