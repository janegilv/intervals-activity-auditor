"""The monitor: an audit run followed by notifications. Meant to be run on a
schedule (launchd on the Mac, cron/systemd/a container elsewhere).

Notification rules:
- Only open, unacknowledged issues are notified, each once. An issue is
  notified again only if it reopens (it came back after being resolved,
  or its acknowledgment was revoked because an activity changed).
- An issue must have been open for NOTIFY_AFTER_MINUTES first. If it goes
  away in the meantime (e.g. Intervals.icu linked the Strava copy of a
  fresh upload) nothing is sent.
- All issues ready in a run go out as one message, grouped by activity.
- An issue counts as notified once at least one notifier accepted it; if
  all fail, it's retried next run.
- If syncing has been failing for HEALTH_ALERT_AFTER_HOURS, one warning is
  sent, and one "working again" message when it recovers.
"""
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from intervals_auditor.db import Database
from intervals_auditor.formatting import activity_heading
from intervals_auditor.notifiers import Notification, NotificationItem, Notifier
from intervals_auditor.notifiers.console import ConsoleNotifier
from intervals_auditor.repositories import IssueRepository, StateRepository
from intervals_auditor.services.queries import STATE_SYNC_FAILING_SINCE, STATE_SYNC_LAST_ERROR, QueryService
from intervals_auditor.services.runner import RunResult, RunService
from intervals_auditor.timeutil import format_local, parse_iso, to_iso
from intervals_auditor.views import ActivityView

log = logging.getLogger(__name__)

STATE_HEALTH_ALERT_SENT = "health_alert_sent_at"


@dataclass
class MonitorResult:
    run: RunResult
    notified: list[ActivityView] = field(default_factory=list)
    waiting: int = 0
    sent_to: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    health: str | None = None  # "alert" | "recovered"

    @property
    def notified_issue_count(self) -> int:
        return sum(len(v.issues) for v in self.notified)


def build_issue_notification(views: list[ActivityView]) -> Notification:
    issue_count = sum(len(v.issues) for v in views)
    items = []
    for view in views:
        lines = []
        for issue in view.issues:
            line = f"[{issue.severity.upper()}] {issue.check}: {issue.summary}"
            if issue.reopen_reason:
                line += f" (reopened: {issue.reopen_reason})"
            lines.append(line)
        items.append(NotificationItem(heading=activity_heading(view.activity, view.activity_id),
                                      url=view.url, link_text=view.activity_id, lines=lines))
    plural_i = "issue" if issue_count == 1 else "issues"
    plural_a = "activity" if len(views) == 1 else "activities"
    return Notification(
        title=f"{issue_count} activity {plural_i} to look at",
        summary=f"New or reopened on {len(views)} {plural_a} in Intervals.icu:",
        items=items,
        footer="Details: python main.py show <id> · Acknowledge: python main.py ack <id>",
    )


class MonitorService:
    def __init__(self, *, db: Database, issues: IssueRepository, state: StateRepository,
                 queries: QueryService, runner: RunService, notifiers: list[Notifier],
                 notify_after_minutes: float = 60, health_alert_after_hours: float = 6, clock):
        self.db = db
        self.issues = issues
        self.state = state
        self.queries = queries
        self.runner = runner
        self.notifiers = notifiers
        self.wait = timedelta(minutes=notify_after_minutes)
        self.health_after = timedelta(hours=health_alert_after_hours)
        self.clock = clock

    def run(self, *, offline: bool = False, dry_run: bool = False) -> MonitorResult:
        run = self.runner.run(command="monitor", offline=offline)
        result = MonitorResult(run=run)
        notifiers = [ConsoleNotifier()] if dry_run else self.notifiers

        ready, result.waiting = self.queries.notifiable(self.clock() - self.wait)
        if result.waiting:
            log.info("%d issue(s) in the %d-minute waiting period before notifying",
                     result.waiting, int(self.wait.total_seconds() // 60))
        if ready:
            count = sum(len(v.issues) for v in ready)
            if not notifiers:
                log.warning("%d issue(s) ready to notify, but no notifiers are configured "
                            "(set NOTIFIERS in .env)", count)
            else:
                result.sent_to, result.errors = self._send(build_issue_notification(ready), notifiers)
                if result.sent_to and not dry_run:
                    with self.db.transaction():
                        self.issues.mark_notified([i.key for v in ready for i in v.issues], to_iso(self.clock()))
                    result.notified = ready
                    log.info("Notified about %d issue(s) on %d activit%s via %s", count, len(ready),
                             "y" if len(ready) == 1 else "ies", ", ".join(result.sent_to))
        else:
            log.info("Nothing new to notify")

        if not offline and not dry_run and notifiers:
            result.health = self._check_health(run, notifiers)
        return result

    def _send(self, notification: Notification, notifiers: list[Notifier]):
        sent_to, errors = [], {}
        for notifier in notifiers:
            try:
                notifier.send(notification)
                sent_to.append(notifier.name)
            except Exception as e:  # noqa: BLE001 - one broken notifier shouldn't stop the others
                errors[notifier.name] = str(e)
                log.error("Notifier %s failed: %s", notifier.name, e)
        return sent_to, errors

    def _check_health(self, run: RunResult, notifiers: list[Notifier]) -> str | None:
        now = self.clock()
        alert_sent = self.state.get(STATE_HEALTH_ALERT_SENT)
        if run.sync_error:
            failing_since = parse_iso(self.state.get(STATE_SYNC_FAILING_SINCE))
            if alert_sent or failing_since is None or now - failing_since < self.health_after:
                return None
            notification = Notification(
                title="Intervals auditor: sync failing",
                summary=(f"Syncing with Intervals.icu has failed since {format_local(failing_since)}. "
                         f"Last error: {self.state.get(STATE_SYNC_LAST_ERROR)}"),
                footer="See data/logs/intervals_auditor.log",
            )
            sent_to, _ = self._send(notification, notifiers)
            if sent_to:
                with self.db.transaction():
                    self.state.set(STATE_HEALTH_ALERT_SENT, to_iso(now))
                log.warning("Sent sync-failing alert")
                return "alert"
        elif alert_sent:
            sent_to, _ = self._send(Notification(title="Intervals auditor: sync working again",
                                                 summary="Syncing with Intervals.icu works again."), notifiers)
            if sent_to:
                with self.db.transaction():
                    self.state.delete(STATE_HEALTH_ALERT_SENT)
                log.info("Sent sync-recovered message")
                return "recovered"
        return None
