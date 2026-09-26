"""Wires settings, database, repositories, clients and services together.

This is the single entry point for any interface: the CLI builds an App
per command today, and a web interface would do the same (one App per
request or per worker) and call the same services.
"""
import logging

from intervals_auditor.config import Settings
from intervals_auditor.db import Database
from intervals_auditor.lock import RunLock
from intervals_auditor.notifiers import Notifier, build_notifiers
from intervals_auditor.repositories import (
    AcknowledgmentRepository, ActivityRepository, CheckSettingRepository, IssueRepository, RunRepository,
    StateRepository,
)
from intervals_auditor.services.acks import AckService
from intervals_auditor.services.audit import AuditService
from intervals_auditor.services.check_settings import CheckSettingsService
from intervals_auditor.services.monitor import MonitorService
from intervals_auditor.services.queries import QueryService
from intervals_auditor.services.runner import RunService
from intervals_auditor.services.sync import SyncService
from intervals_auditor.sources.intervals import IntervalsClient
from intervals_auditor.sources.strava import StravaClient
from intervals_auditor.timeutil import utcnow

log = logging.getLogger(__name__)

# Strava rotates refresh tokens; the latest one is kept in the database,
# together with the .env token it was derived from.
STATE_STRAVA_REFRESH_TOKEN = "strava_refresh_token"
STATE_STRAVA_REFRESH_TOKEN_FROM = "strava_refresh_token_env"


class App:
    def __init__(self, settings: Settings, *, clock=utcnow, intervals_client=None, strava_client=None,
                 notifiers: list[Notifier] | None = None):
        self.settings = settings
        self.clock = clock
        self._intervals_client = intervals_client
        self._strava_client = strava_client
        self._notifiers = notifiers

        self.db = Database(settings.db_path)
        self.activities = ActivityRepository(self.db)
        self.issues = IssueRepository(self.db)
        self.acks = AcknowledgmentRepository(self.db)
        self.state = StateRepository(self.db)
        self.runs = RunRepository(self.db)

        self.queries = QueryService(self.activities, self.issues, self.acks, self.state, self.runs)
        # checks.toml / checks.local.toml, plus changes made in the web interface.
        self.check_settings = CheckSettingsService(self.db, CheckSettingRepository(self.db), settings.checks,
                                                   clock=clock)
        self.audit = AuditService(self.db, self.activities, self.issues, self.acks,
                                  self.check_settings.effective(), clock=clock)
        self.ack_service = AckService(self.db, self.activities, self.issues, self.acks, clock=clock)
        self.runner = RunService(
            db=self.db, activities=self.activities, runs=self.runs, state=self.state, audit=self.audit,
            queries=self.queries, sync_factory=self.sync_service, strava_factory=self.strava_client,
            output_dir=settings.output_dir, clock=clock,
        )

    def intervals_client(self):
        if self._intervals_client is None:
            self.settings.require_intervals()
            self._intervals_client = IntervalsClient(self.settings.intervals_api_key,
                                                     self.settings.intervals_athlete_id,
                                                     self.settings.request_timeout)
        return self._intervals_client

    def strava_client(self):
        if self._strava_client is None and self.settings.has_strava_credentials:
            s = self.settings
            self._strava_client = StravaClient(s.strava_client_id, s.strava_client_secret,
                                               self.strava_refresh_token(), s.request_timeout,
                                               on_refresh_token=self.store_strava_refresh_token)
        return self._strava_client

    def strava_refresh_token(self) -> str:
        """The latest refresh token Strava gave us, unless STRAVA_REFRESH_TOKEN
        in .env has changed since (you re-authorized): then that one wins."""
        env_token = self.settings.strava_refresh_token
        stored = self.state.get(STATE_STRAVA_REFRESH_TOKEN)
        if stored and self.state.get(STATE_STRAVA_REFRESH_TOKEN_FROM) == env_token:
            return stored
        return env_token

    def store_strava_refresh_token(self, token: str) -> None:
        with self.db.transaction():
            self.state.set(STATE_STRAVA_REFRESH_TOKEN, token)
            self.state.set(STATE_STRAVA_REFRESH_TOKEN_FROM, self.settings.strava_refresh_token)
        log.info("Stored Strava's new refresh token in the database")

    def sync_service(self) -> SyncService:
        return SyncService(self.db, self.activities, self.state, self.intervals_client(),
                           refresh_days=self.settings.refresh_days,
                           full_sync_interval_hours=self.settings.full_sync_interval_hours, clock=self.clock)

    def notifiers(self) -> list[Notifier]:
        if self._notifiers is None:
            self._notifiers = build_notifiers(self.settings.notifier_names, self.settings.env)
        return self._notifiers

    def monitor(self, *, dry_run: bool = False) -> MonitorService:
        return MonitorService(
            db=self.db, issues=self.issues, state=self.state, queries=self.queries, runner=self.runner,
            notifiers=[] if dry_run else self.notifiers(),
            notify_after_minutes=self.settings.notify_after_minutes,
            health_alert_after_hours=self.settings.health_alert_after_hours, clock=self.clock,
        )

    def refresh_reports(self) -> list:
        """Rewrite the report files after a change made outside a run (e.g.
        ack/unack), so they match the database. The reports are only a view
        of the database: failing to write them is logged and never undoes
        the change itself."""
        try:
            return self.runner.write_reports()
        except OSError as e:
            log.warning("Could not update the reports in %s: %s", self.settings.output_dir, e)
            return []

    def lock(self) -> RunLock:
        return RunLock(self.settings.lock_path)

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
