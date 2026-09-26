"""The web interface: a JSON API over the application services, plus the
single-page frontend (static/index.html + app.js) that uses it.

Every request gets its own App (and database connection); slow work
(syncs, re-running checks, the scheduled monitor) runs as background jobs,
see jobs.py. Interactive API docs are at /api/docs.
"""
import logging
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from intervals_auditor import __version__
from intervals_auditor.app import App
from intervals_auditor.checks import CHECKS
from intervals_auditor.config import Settings
from intervals_auditor.reporting import html_report
from intervals_auditor.reporting.json_report import activity_to_dict
from intervals_auditor.services.acks import NotFoundError
from intervals_auditor.services.check_settings import CheckSettingsError
from intervals_auditor.timeutil import to_iso, utcnow
from intervals_auditor.ui import ASSETS
from intervals_auditor.web.jobs import JobBusy, JobManager, MonitorScheduler
from intervals_auditor.web.security import require_access
from intervals_auditor.web.serializers import (
    ack_dict, activity_detail_dict, check_settings_dict, run_dict, status_dict,
)

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
SHARED_ASSETS = {"base.css": "text/css", "issues.js": "text/javascript"}


class AckRequest(BaseModel):
    issues: list[int] = Field(default_factory=list, description="Issue numbers")
    activities: list[str] = Field(default_factory=list, description="Activity ids (all their open issues)")
    note: str = ""
    check: str | None = Field(None, description="With activities: only this check's issues")
    only_this: bool = Field(False, description="Don't include the mirrored issue on related activities")


class UnackRequest(BaseModel):
    issues: list[int] = Field(default_factory=list)
    activities: list[str] = Field(default_factory=list)
    check: str | None = None
    only_this: bool = False


class JobRequest(BaseModel):
    kind: Literal["sync", "resync", "checks"]


class CheckUpdate(BaseModel):
    values: dict[str, Any]


def create_app(settings: Settings, *, app_factory=App, scheduler: bool = True, clock=utcnow) -> FastAPI:
    jobs = JobManager(settings, app_factory, clock)
    monitor = MonitorScheduler(jobs, settings.monitor_interval_minutes if scheduler else 0, clock=clock)

    @asynccontextmanager
    async def lifespan(_):
        monitor.start()
        yield
        monitor.stop()

    web = FastAPI(title="Intervals auditor", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    web.state.settings, web.state.jobs, web.state.monitor = settings, jobs, monitor

    def get_app() -> Iterator[App]:
        app = app_factory(settings)
        try:
            yield app
        finally:
            app.close()

    def start_job(kind: str):
        try:
            return jobs.start(kind).as_dict()
        except JobBusy:
            return None

    api = APIRouter(prefix="/api", dependencies=[Depends(require_access)])

    @api.get("/status", summary="Sync health, counts, notifiers, schedule and jobs")
    def status(app: App = Depends(get_app)):
        return {
            **status_dict(app.queries.status()),
            "now": to_iso(clock()),
            "version": __version__,
            "notifiers": settings.notifier_names,
            "notify_after_minutes": settings.notify_after_minutes,
            "credentials": {"intervals": settings.has_intervals_credentials, "strava": settings.has_strava_credentials},
            "monitor": monitor.as_dict(),
            "streams": app.stream_status(),
            "jobs": jobs.status(),
        }

    @api.get("/issues", summary="Open and acknowledged issues, grouped by activity")
    def issues(app: App = Depends(get_app)):
        return {
            "generated_at": to_iso(clock()),
            "last_sync": app.queries.report_meta().get("last_sync"),
            "checks": {name: cls.description for name, cls in CHECKS.items()},
            "activities": [activity_to_dict(v) for v in app.queries.report_views()],
        }

    @api.get("/activities/{ref}", summary="One activity (id, or an issue number) with issue history and acknowledgments")
    def activity(ref: str, app: App = Depends(get_app)):
        target = app.queries.resolve_ref(ref)
        if target is None:
            raise HTTPException(404, f"No activity or issue {ref!r}")
        kind, value = target
        activity_id = value if kind == "activity" else app.issues.get_by_number(value).activity_id
        return activity_detail_dict(app.queries.activity_detail(activity_id))

    def resolve(app: App, numbers: list[int], activity_refs: list[str], check: str | None):
        if check is not None and check not in CHECKS:
            raise HTTPException(422, f"Unknown check {check!r}")
        missing = [str(n) for n in numbers if app.issues.get_by_number(n) is None]
        activity_ids = []
        for ref in activity_refs:
            activity_id = app.queries.resolve_activity_id(ref)
            (activity_ids.append(activity_id) if activity_id else missing.append(ref))
        if missing:
            raise HTTPException(404, f"No issue number or activity: {', '.join(missing)}")
        if not numbers and not activity_ids:
            raise HTTPException(422, "Nothing given: pass issue numbers and/or activity ids")
        return activity_ids

    @api.post("/acks", summary="Acknowledge issues")
    def acknowledge(req: AckRequest, app: App = Depends(get_app)):
        activity_ids = resolve(app, req.issues, req.activities, req.check)
        created = []
        try:
            for number in req.issues:
                created += app.ack_service.acknowledge_issue(number, note=req.note, include_related=not req.only_this)
            for activity_id in activity_ids:
                created += app.ack_service.acknowledge(activity_id, check=req.check, note=req.note,
                                                       include_related=not req.only_this)
        except NotFoundError as e:
            raise HTTPException(404, str(e))
        app.refresh_reports()
        return {"count": len(created), "created": [ack_dict(a) for a in created]}

    @api.post("/unacks", summary="Revoke acknowledgments (report the issues again)")
    def unacknowledge(req: UnackRequest, app: App = Depends(get_app)):
        activity_ids = resolve(app, req.issues, req.activities, req.check)
        revoked = []
        for number in req.issues:
            revoked += app.ack_service.unacknowledge_issue(number, include_related=not req.only_this)
        for activity_id in activity_ids:
            revoked += app.ack_service.unacknowledge(activity_id, check=req.check, include_related=not req.only_this)
        app.refresh_reports()
        return {"count": len(revoked), "revoked": [ack_dict(a) for a in revoked]}

    @api.get("/acks", summary="Acknowledgments")
    def acknowledgments(include_revoked: bool = False, activity: str | None = None, app: App = Depends(get_app)):
        activity_id = None
        if activity:
            activity_id = app.queries.resolve_activity_id(activity)
            if activity_id is None:
                raise HTTPException(404, f"No activity {activity!r}")
        return {"acks": [ack_dict(a) for a in app.ack_service.list(include_revoked=include_revoked,
                                                                    activity_id=activity_id)]}

    @api.get("/jobs", summary="The running job and the last finished one")
    def job_status():
        return jobs.status()

    @api.post("/jobs", status_code=202, summary="Start a sync, full resync or re-run of the checks")
    def create_job(req: JobRequest):
        try:
            return {"job": jobs.start(req.kind).as_dict()}
        except JobBusy as e:
            raise HTTPException(409, str(e))

    @api.get("/runs", summary="Recent runs (CLI, scheduled monitor and web jobs)")
    def runs(limit: int = Query(20, ge=1, le=200), app: App = Depends(get_app)):
        return {"runs": [run_dict(r) for r in app.runs.recent(limit)]}

    @api.get("/logs", summary="The last lines of the log file")
    def logs(lines: int = Query(200, ge=1, le=5000)):
        path = settings.log_file
        if not path.exists():
            return {"file": str(path), "lines": []}
        with path.open(encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=lines)
        return {"file": str(path), "lines": [line.rstrip("\n") for line in tail]}

    @api.get("/checks", summary="Check settings: file values and changes made here")
    def checks(app: App = Depends(get_app)):
        return {"checks": [check_settings_dict(c) for c in app.check_settings.describe()]}

    def changed_settings(fn, name: str):
        try:
            check = fn()
        except CheckSettingsError as e:
            raise HTTPException(404 if name not in CHECKS else 422, str(e))
        job = start_job("checks")
        message = ("Saved. Re-running the checks with the new settings." if job else
                   "Saved. The checks use the new settings from the next run (a job is running now).")
        return {"check": check_settings_dict(check), "job": job, "message": message}

    @api.put("/checks/{name}", summary="Change settings of a check (and re-run the checks)")
    def update_check(name: str, req: CheckUpdate, app: App = Depends(get_app)):
        return changed_settings(lambda: app.check_settings.update(name, req.values), name)

    @api.delete("/checks/{name}", summary="Reset a check to the configuration files (and re-run the checks)")
    def reset_check(name: str, app: App = Depends(get_app)):
        return changed_settings(lambda: app.check_settings.reset(name), name)

    web.include_router(api)

    @web.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @web.get("/report.html", response_class=HTMLResponse, dependencies=[Depends(require_access)],
             include_in_schema=False)
    def static_report(app: App = Depends(get_app)):
        return html_report.render(app.queries.report_views(), to_iso(clock()), app.queries.report_meta())

    @web.get("/ui/{name}", include_in_schema=False)
    def shared_asset(name: str):
        if name not in SHARED_ASSETS:
            raise HTTPException(404)
        return Response((ASSETS / name).read_text(encoding="utf-8"), media_type=SHARED_ASSETS[name],
                        headers={"Cache-Control": "no-cache"})

    web.mount("/static", StaticFiles(directory=STATIC), name="static")

    @web.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return web
