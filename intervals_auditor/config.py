"""Settings, read from environment variables (usually via .env) and
checks.toml (+ the optional, untracked checks.local.toml).

Relative paths are resolved against the project directory, so the tool
behaves the same whatever directory it's started from (launchd, cron, a
container...). Nothing is required just to load settings: the Intervals.icu
credentials are only demanded by the commands that sync.
"""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from intervals_auditor.errors import ConfigError
from intervals_auditor.notifiers import parse_names

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    project_root: Path
    intervals_api_key: str
    intervals_athlete_id: str
    data_dir: Path
    db_path: Path
    log_file: Path
    output_dir: Path
    refresh_days: int
    full_sync_interval_hours: float
    request_timeout: int
    notify_after_minutes: float
    health_alert_after_hours: float
    log_level: str
    notifier_names: list[str]
    checks: dict
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_refresh_token: str = ""
    # Web interface (`serve`): where to listen, and how often the built-in
    # scheduler runs the monitor (0 turns the scheduler off).
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    monitor_interval_minutes: float = 30.0
    env: dict = field(default_factory=dict, repr=False)

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "intervals_auditor.lock"

    @property
    def has_intervals_credentials(self) -> bool:
        return bool(self.intervals_api_key and self.intervals_athlete_id)

    @property
    def has_strava_credentials(self) -> bool:
        return bool(self.strava_client_id and self.strava_client_secret and self.strava_refresh_token)

    def require_intervals(self) -> None:
        if not self.has_intervals_credentials:
            raise ConfigError("INTERVALS_API_KEY and INTERVALS_ATHLETE_ID must be set in .env to sync "
                              "(copy .env.example to .env). Use --offline to work with stored data only.")


def _number(env, name, default, kind=int):
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None


def load_checks(root: Path, env) -> dict:
    path = root / (env.get("CHECKS_FILE") or "checks.toml")
    try:
        with open(path, "rb") as f:
            checks = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Check configuration {path} not found") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None

    # Personal, account-specific overrides (e.g. excluding one of your own
    # activities from a check) live in checks.local.toml instead of the
    # tracked checks.toml. Each section there is merged on top of the
    # matching section in checks.toml, one level deep.
    local_path = Path(env.get("CHECKS_LOCAL_FILE") or root / "checks.local.toml")
    if not local_path.is_absolute():
        local_path = root / local_path
    if local_path.exists():
        try:
            with open(local_path, "rb") as f:
                local_checks = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{local_path}: {e}") from None
        for name, overrides in local_checks.items():
            checks.setdefault(name, {}).update(overrides)
    return checks


def load_settings(root: Path = PROJECT_ROOT, environ=None) -> Settings:
    if environ is None:
        load_dotenv(root / ".env")
        environ = os.environ
    env = dict(environ)

    def path(name, default) -> Path:
        value = Path((env.get(name) or "").strip() or default).expanduser()
        return value if value.is_absolute() else root / value

    data_dir = path("DATA_DIR", "data")
    return Settings(
        project_root=root,
        intervals_api_key=(env.get("INTERVALS_API_KEY") or "").strip(),
        intervals_athlete_id=(env.get("INTERVALS_ATHLETE_ID") or "").strip(),
        data_dir=data_dir,
        db_path=path("DB_PATH", str(data_dir / "intervals_auditor.sqlite")),
        log_file=path("LOG_FILE", str(data_dir / "logs" / "intervals_auditor.log")),
        output_dir=path("OUTPUT_DIR", "output"),
        refresh_days=_number(env, "REFRESH_DAYS", 30),
        full_sync_interval_hours=_number(env, "FULL_SYNC_INTERVAL_HOURS", 24.0, float),
        request_timeout=_number(env, "REQUEST_TIMEOUT_SECONDS", 30),
        notify_after_minutes=_number(env, "NOTIFY_AFTER_MINUTES", 60.0, float),
        health_alert_after_hours=_number(env, "HEALTH_ALERT_AFTER_HOURS", 6.0, float),
        log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        notifier_names=parse_names(env.get("NOTIFIERS")),
        checks=load_checks(root, env),
        strava_client_id=(env.get("STRAVA_CLIENT_ID") or "").strip(),
        strava_client_secret=(env.get("STRAVA_CLIENT_SECRET") or "").strip(),
        strava_refresh_token=(env.get("STRAVA_REFRESH_TOKEN") or "").strip(),
        web_host=(env.get("WEB_HOST") or "127.0.0.1").strip(),
        web_port=_number(env, "WEB_PORT", 8080),
        monitor_interval_minutes=_number(env, "MONITOR_INTERVAL_MINUTES", 30.0, float),
        env=env,
    )
