"""Background work for the web interface.

JobManager runs one job at a time in a background thread: a sync started
from the browser, re-running the checks after a settings change, or the
scheduled monitor. Each job gets its own App (and so its own database
connection) and takes the same run lock as the CLI, so a `docker compose
exec … python main.py run` and a job never sync at the same time.

MonitorScheduler runs the monitor (sync, checks, notifications) every
MONITOR_INTERVAL_MINUTES, like launchd does on the Mac.
"""
import itertools
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Callable

from intervals_auditor.app import App
from intervals_auditor.config import Settings
from intervals_auditor.lock import AlreadyRunning
from intervals_auditor.services.runner import RunResult, describe_error
from intervals_auditor.timeutil import to_iso, utcnow

log = logging.getLogger(__name__)


class JobBusy(RuntimeError):
    pass


@dataclass
class Job:
    id: int
    kind: str
    started_at: str
    status: str = "running"  # running | ok | degraded | failed | skipped
    finished_at: str | None = None
    message: str = ""
    result: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def summarize_run(result: RunResult) -> str:
    parts = []
    if result.sync:
        parts.append(f"synced {result.sync.fetched} activities ({len(result.sync.inserted)} new, "
                     f"{len(result.sync.changed)} changed, {len(result.sync.removed)} removed)")
    elif result.sync_error:
        parts.append(f"sync failed: {result.sync_error}")
    if result.audit:
        a = result.audit
        parts.append(f"{len(a.new)} new, {len(a.reopened)} reopened and {len(a.resolved)} resolved issues")
    if result.strava_error:
        parts.append(result.strava_error)
    text = "; ".join(parts) or "done"
    return text[0].upper() + text[1:]


def _run_job(kind: str) -> Callable[[App], tuple[str, str, dict]]:
    """What each kind of job does. Returns (status, message, details)."""
    def run(app: App, **kwargs):
        result = app.runner.run(command=f"web-{kind}", **kwargs)
        status = "degraded" if (result.sync_error or result.strava_error) else "ok"
        return status, summarize_run(result), result.as_dict()

    if kind == "sync":
        return lambda app: run(app)
    if kind == "resync":
        return lambda app: run(app, full=True)
    if kind == "checks":
        return lambda app: run(app, offline=True)
    if kind == "monitor":
        def monitor(app: App):
            result = app.monitor().run()
            message = summarize_run(result.run)
            if result.notified:
                message += f"; notified about {result.notified_issue_count} issue(s) via {', '.join(result.sent_to)}"
            if result.errors:
                message += "; notifier errors: " + ", ".join(f"{k}: {v}" for k, v in result.errors.items())
            status = "degraded" if (result.run.sync_error or result.errors) else "ok"
            return status, message, {"run": result.run.as_dict(), "notified": result.notified_issue_count,
                                     "waiting": result.waiting, "errors": result.errors}
        return monitor
    raise ValueError(f"Unknown job kind {kind!r}")


JOB_KINDS = ("sync", "resync", "checks", "monitor")


class JobManager:
    def __init__(self, settings: Settings, app_factory: Callable[[Settings], App] = App, clock=utcnow):
        self.settings = settings
        self.app_factory = app_factory
        self.clock = clock
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self.current: Job | None = None
        self.last: Job | None = None
        self._thread: threading.Thread | None = None

    def status(self) -> dict:
        with self._lock:
            return {"current": self.current.as_dict() if self.current else None,
                    "last": self.last.as_dict() if self.last else None}

    def _claim(self, kind: str) -> Job:
        with self._lock:
            if self.current is not None:
                raise JobBusy(f"A {self.current.kind} job is already running")
            self.current = Job(id=next(self._ids), kind=kind, started_at=to_iso(self.clock()))
            return self.current

    def start(self, kind: str) -> Job:
        """Start a job in the background; raises JobBusy if one is running."""
        fn = _run_job(kind)
        job = self._claim(kind)
        self._thread = threading.Thread(target=self._execute, args=(job, fn), name=f"job-{kind}", daemon=True)
        self._thread.start()
        return job

    def run(self, kind: str) -> Job:
        """Run a job in the calling thread (the scheduler's); raises JobBusy."""
        fn = _run_job(kind)
        job = self._claim(kind)
        self._execute(job, fn)
        return job

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _execute(self, job: Job, fn) -> None:
        log.info("Job %d (%s) started", job.id, job.kind)
        try:
            with self.app_factory(self.settings) as app:
                with app.lock():
                    job.status, job.message, job.result = fn(app)
        except AlreadyRunning:
            job.status, job.message = "skipped", "Another run is in progress (for example from the command line)."
        except Exception as e:  # noqa: BLE001 - reported to the browser, logged with traceback
            job.status, job.message = "failed", describe_error(e)
            log.exception("Job %d (%s) failed", job.id, job.kind)
        job.finished_at = to_iso(self.clock())
        log.info("Job %d (%s) %s: %s", job.id, job.kind, job.status, job.message)
        with self._lock:
            self.current, self.last = None, job


class MonitorScheduler:
    """Runs the monitor every `interval_minutes` in a background thread."""

    def __init__(self, jobs: JobManager, interval_minutes: float, *, first_delay_seconds: float = 60, clock=utcnow):
        self.jobs = jobs
        self.interval = timedelta(minutes=interval_minutes)
        self.first_delay = timedelta(seconds=first_delay_seconds)
        self.clock = clock
        self.next_run_at = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.interval.total_seconds() > 0

    def start(self) -> None:
        if not self.enabled:
            log.info("Scheduled monitor is off (MONITOR_INTERVAL_MINUTES=0)")
            return
        self.next_run_at = self.clock() + self.first_delay
        self._thread = threading.Thread(target=self._loop, name="monitor-scheduler", daemon=True)
        self._thread.start()
        log.info("Scheduled monitor every %g minutes, first run at %s",
                 self.interval.total_seconds() / 60, to_iso(self.next_run_at))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait = (self.next_run_at - self.clock()).total_seconds()
            if wait > 0 and self._stop.wait(wait):
                return
            try:
                self.jobs.run("monitor")
            except JobBusy as e:
                log.info("Scheduled monitor skipped: %s", e)
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                log.exception("Scheduled monitor failed")
            self.next_run_at = self.clock() + self.interval

    def as_dict(self) -> dict:
        return {"enabled": self.enabled, "interval_minutes": self.interval.total_seconds() / 60,
                "next_run_at": to_iso(self.next_run_at) if self.next_run_at else None}
