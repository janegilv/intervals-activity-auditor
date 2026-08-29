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

def load_settings():
    api_key = os.getenv("INTERVALS_API_KEY", "").strip()
    athlete_id = os.getenv("INTERVALS_ATHLETE_ID", "").strip()
    if not api_key or not athlete_id:
        raise RuntimeError(
            "Missing INTERVALS_API_KEY or INTERVALS_ATHLETE_ID. Copy .env.example to .env and fill them in."
        )
    with open("checks.toml", "rb") as f:
        checks = tomllib.load(f)
    return Settings(
        api_key=api_key,
        athlete_id=athlete_id,
        cache_db=Path(os.getenv("CACHE_DB", "cache/intervals.sqlite")),
        refresh_days=int(os.getenv("REFRESH_DAYS", "30")),
        timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
        checks=checks,
    )
