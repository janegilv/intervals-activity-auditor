"""Wires settings, database, repositories, clients and services together.

This is the single entry point for any interface: the CLI builds an App
per command today, and a web interface would do the same (one App per
request or per worker) and call the same services.
"""
import logging

from auditor.config import Settings
from auditor.db import Database, copy_database
from auditor.lock import RunLock
from auditor.notifiers import Notifier, build_notifiers
from auditor.repositories import (
    AcknowledgmentRepository, ActivityRepository, IssueRepository, RunRepository, StateRepository,
)
from auditor.services.acks import AckService
from auditor.services.audit import AuditService
from auditor.services.monitor import MonitorService
from auditor.services.queries import QueryService
from auditor.services.runner import RunService
from auditor.services.sync import SyncService
from auditor.sources.intervals import IntervalsClient
from auditor.sources.strava import StravaClient
from auditor.timeutil import utcnow

log = logging.getLogger(__name__)


def prepare_database(settings: Settings) -> bool:
    """First start after upgrading: copy the old cache database (with its
    Strava data) to the new location. The old file is left untouched."""
    if settings.db_path.exists() or not settings.legacy_db_path.exists():
        return False
    copy_database(settings.legacy_db_path, settings.db_path)
    log.warning("Copied %s to %s; the application uses the new file from now on. "
                "The old one is no longer used and can be deleted.",
                settings.legacy_db_path, settings.db_path)
    return True


class App:
    def __init__(self, settings: Settings, *, clock=utcnow, intervals_client=None, strava_client=None,
                 notifiers: list[Notifier] | None = None):
        self.settings = settings
        self.clock = clock
        self._intervals_client = intervals_client
        self._strava_client = strava_client
        self._notifiers = notifiers

        prepare_database(settings)
        self.db = Database(settings.db_path)
        self.activities = ActivityRepository(self.db)
        self.issues = IssueRepository(self.db)
        self.acks = AcknowledgmentRepository(self.db)
        self.state = StateRepository(self.db)
        self.runs = RunRepository(self.db)

        self.queries = QueryService(self.activities, self.issues, self.acks, self.state, self.runs)
        self.audit = AuditService(self.db, self.activities, self.issues, self.acks, settings.checks, clock=clock)
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
                                               s.strava_refresh_token, s.request_timeout)
        return self._strava_client

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

    def lock(self) -> RunLock:
        return RunLock(self.settings.lock_path)

    def close(self) -> None:
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
