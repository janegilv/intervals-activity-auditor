"""One audit run: sync from Intervals.icu, fill in Strava placeholders, run
the checks, and write the reports. Used by both `run` and `monitor`.

A failing sync (network down, Intervals.icu unavailable) doesn't stop the
run -- the checks still run on the stored data -- but it's logged, kept in
the run history, and tracked so the monitor can warn if it keeps failing.
Missing configuration (ConfigError) does stop the run.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from intervals_auditor.db import Database
from intervals_auditor.errors import ConfigError
from intervals_auditor.reporting import write_reports
from intervals_auditor.repositories import ActivityRepository, RunRepository, StateRepository
from intervals_auditor.services.audit import AuditResult, AuditService
from intervals_auditor.services.queries import STATE_SYNC_FAILING_SINCE, STATE_SYNC_LAST_ERROR, QueryService
from intervals_auditor.services.strava import EnrichResult, enrich_strava_stubs
from intervals_auditor.services.sync import SyncResult, SyncService
from intervals_auditor.timeutil import to_iso

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_id: int
    command: str
    offline: bool
    sync: SyncResult | None = None
    sync_error: str | None = None
    strava: EnrichResult | None = None
    strava_error: str | None = None
    audit: AuditResult | None = None
    report_paths: list[Path] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "offline": self.offline,
            "sync": self.sync.as_dict() if self.sync else None,
            "sync_error": self.sync_error,
            "strava": self.strava.as_dict() if self.strava else None,
            "strava_error": self.strava_error,
            "audit": self.audit.as_dict() if self.audit else None,
        }


def describe_error(error: Exception) -> str:
    response = getattr(error, "response", None)
    if response is not None and getattr(response, "status_code", None) in (401, 403):
        return f"Intervals.icu refused the credentials (HTTP {response.status_code}); check INTERVALS_API_KEY/INTERVALS_ATHLETE_ID"
    return f"{type(error).__name__}: {error}"


class RunService:
    def __init__(self, *, db: Database, activities: ActivityRepository, runs: RunRepository,
                 state: StateRepository, audit: AuditService, queries: QueryService,
                 sync_factory: Callable[[], SyncService], strava_factory: Callable[[], object | None],
                 output_dir: Path, clock):
        self.db = db
        self.activities = activities
        self.runs = runs
        self.state = state
        self.audit = audit
        self.queries = queries
        self.sync_factory = sync_factory
        self.strava_factory = strava_factory
        self.output_dir = output_dir
        self.clock = clock

    def run(self, *, command: str = "run", offline: bool = False, only: list[str] | None = None,
            full: bool | None = None, force_prune: bool = False, reports: bool = True) -> RunResult:
        with self.db.transaction():
            run_id = self.runs.start(command, to_iso(self.clock()))
        result = RunResult(run_id=run_id, command=command, offline=offline)
        log.info("=== %s started (run #%d)%s", command, run_id, " offline" if offline else "")
        try:
            if not offline:
                self._sync(result, full=full, force_prune=force_prune)
            result.audit = self.audit.run(only=only)
            if reports:
                result.report_paths = self.write_reports()
        except BaseException as e:
            with self.db.transaction():
                self.runs.finish(run_id, to_iso(self.clock()), "failed", result.as_dict(), describe_error(e))
            if not isinstance(e, ConfigError):  # the CLI reports configuration errors itself
                log.error("=== %s failed (run #%d): %s", command, run_id, describe_error(e))
            raise
        status = "ok" if not (result.sync_error or result.strava_error) else "degraded"
        with self.db.transaction():
            self.runs.finish(run_id, to_iso(self.clock()), status, result.as_dict(),
                             result.sync_error or result.strava_error)
        log.info("=== %s finished (run #%d, %s)", command, run_id, status)
        return result

    def write_reports(self) -> list[Path]:
        """Write report.html/md/json/csv from what's in the database."""
        paths = write_reports(self.queries.report_views(), self.output_dir, to_iso(self.clock()),
                              self.queries.report_meta())
        log.debug("Reports written to %s", self.output_dir)
        return paths

    def _sync(self, result: RunResult, *, full: bool | None, force_prune: bool) -> None:
        sync_service = self.sync_factory()  # ConfigError propagates: nothing to do without credentials
        try:
            result.sync = sync_service.sync(full=full, force_prune=force_prune)
        except ConfigError:
            raise
        except Exception as e:  # noqa: BLE001 - keep going with stored data
            result.sync_error = describe_error(e)
            log.error("Sync failed, continuing with stored data: %s", result.sync_error)
            with self.db.transaction():
                if not self.state.get(STATE_SYNC_FAILING_SINCE):
                    self.state.set(STATE_SYNC_FAILING_SINCE, to_iso(self.clock()))
                self.state.set(STATE_SYNC_LAST_ERROR, result.sync_error)
            return
        with self.db.transaction():
            self.state.delete(STATE_SYNC_FAILING_SINCE)
            self.state.delete(STATE_SYNC_LAST_ERROR)

        strava_client = self.strava_factory()
        if strava_client is None:
            log.debug("Strava credentials not configured; skipping Strava enrichment")
            return
        try:
            result.strava = enrich_strava_stubs(self.db, self.activities, strava_client, clock=self.clock)
        except Exception as e:  # noqa: BLE001 - e.g. a revoked Strava token
            result.strava_error = f"Strava: {describe_error(e)}"
            log.error("Strava enrichment failed: %s", e)
