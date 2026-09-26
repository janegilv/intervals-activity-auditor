"""Run the checks over every activity and keep the issues table up to date.

For each issue the checks find:
- not seen before            -> stored as a new open issue
- previously resolved        -> reopened (and will be notified about again)
- already open               -> refreshed (summary/severity may change)
Open issues the checks no longer find are marked resolved -- the activity
was fixed or deleted.

Before that, every active acknowledgment is compared with the activities
it covers. If one of them has changed (different fingerprint) or been
deleted since it was acknowledged, the acknowledgment is revoked, and if
the issue is still there it's treated as reopened.
"""
import logging
from dataclasses import dataclass, field

from auditor.checks import CHECKS
from auditor.db import Database
from auditor.models import SEVERITY_RANK, Acknowledgment, Activity, Issue, IssueRecord
from auditor.repositories import AcknowledgmentRepository, ActivityRepository, IssueRepository
from auditor.timeutil import to_iso

log = logging.getLogger(__name__)

REOPENED_FOUND_AGAIN = "found again after it was resolved"


class UnknownCheckError(ValueError):
    pass


@dataclass
class AuditResult:
    checks_run: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    new: list[IssueRecord] = field(default_factory=list)
    reopened: list[IssueRecord] = field(default_factory=list)
    resolved: list[IssueRecord] = field(default_factory=list)
    revoked_acks: list[Acknowledgment] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"checks": self.counts, "new": len(self.new), "reopened": len(self.reopened),
                "resolved": len(self.resolved), "revoked_acks": len(self.revoked_acks)}


class AuditService:
    def __init__(self, db: Database, activities: ActivityRepository, issues: IssueRepository,
                 acks: AcknowledgmentRepository, check_config: dict, *, clock):
        self.db = db
        self.activities = activities
        self.issues = issues
        self.acks = acks
        self.check_config = check_config
        self.clock = clock

    def enabled_checks(self) -> list[str]:
        return [name for name in CHECKS if self.check_config.get(name, {}).get("enabled", False)]

    def run_checks(self, activities: list[Activity], names: list[str]) -> tuple[dict[str, Issue], dict[str, int]]:
        found: dict[str, Issue] = {}
        counts: dict[str, int] = {}
        for name in names:
            check = CHECKS[name](self.check_config.get(name, {}))
            issues = check.run(activities)
            counts[name] = len(issues)
            log.debug("Check %s: %d issue(s)", name, len(issues))
            for issue in issues:
                previous = found.get(issue.key)
                if previous is None or SEVERITY_RANK.get(issue.severity, 99) < SEVERITY_RANK.get(previous.severity, 99):
                    found[issue.key] = issue
        return found, counts

    def run(self, only: list[str] | None = None) -> AuditResult:
        """Run the enabled checks (or just `only`, enabled or not) on all
        activities and record the outcome. With `only`, issues from other
        checks are left untouched."""
        unknown = [name for name in (only or []) if name not in CHECKS]
        if unknown:
            raise UnknownCheckError(f"Unknown check(s): {', '.join(unknown)}. Available: {', '.join(CHECKS)}")
        names = list(only) if only else self.enabled_checks()
        now = to_iso(self.clock())
        activities = self.activities.all()
        by_id = {a.id: a for a in activities}
        log.info("Running %d check(s) on %d activities: %s", len(names), len(activities), ", ".join(names))
        found, counts = self.run_checks(activities, names)
        result = AuditResult(checks_run=names, counts=counts)

        new_keys, reopened_keys, resolved_keys = [], [], []
        with self.db.transaction():
            result.revoked_acks = self._revoke_stale_acks(by_id, now)

            # A normal run owns every stored issue, including those of checks
            # that have since been disabled; an --only run just its checks.
            existing = {r.key: r for r in self.issues.list(checks=names if only else None)}
            for key, issue in found.items():
                record = existing.get(key)
                if record is None:
                    self.issues.insert(issue, now)
                    new_keys.append(key)
                elif not record.is_open:
                    self.issues.reopen(issue, now, REOPENED_FOUND_AGAIN)
                    reopened_keys.append(key)
                else:
                    self.issues.refresh(issue, now)
            for key, record in existing.items():
                if record.is_open and key not in found:
                    self.issues.resolve(key, now)
                    resolved_keys.append(key)

            for ack in result.revoked_acks:
                if ack.issue_key in found and ack.issue_key not in new_keys + reopened_keys:
                    self.issues.restart_notification(ack.issue_key, now, f"acknowledgment revoked: {ack.revoked_reason}")
                    reopened_keys.append(ack.issue_key)

        result.new = [self.issues.get(k) for k in new_keys]
        result.reopened = [self.issues.get(k) for k in reopened_keys]
        result.resolved = [self.issues.get(k) for k in resolved_keys]
        self._log_result(result, by_id)
        return result

    def _revoke_stale_acks(self, by_id: dict[str, Activity], now: str) -> list[Acknowledgment]:
        revoked = []
        for ack in self.acks.list():
            deleted = [aid for aid in ack.involved_activity_ids if aid not in by_id]
            changed = [aid for aid in ack.involved_activity_ids
                       if aid in by_id and by_id[aid].fingerprint != ack.fingerprints.get(aid)]
            if deleted:
                reason = f"activity {', '.join(deleted)} was deleted"
            elif changed:
                reason = f"activity {', '.join(changed)} changed since it was acknowledged"
            else:
                continue
            self.acks.revoke(ack.id, now, reason)
            ack.revoked_at, ack.revoked_reason = now, reason
            revoked.append(ack)
        return revoked

    @staticmethod
    def _log_result(result: AuditResult, by_id: dict[str, Activity]) -> None:
        def label(record: IssueRecord) -> str:
            a = by_id.get(record.activity_id)
            where = f"{a.local_start} {a.activity_type} {a.name!r}" if a else "(deleted)"
            return f"{record.activity_id} {where} [{record.severity}] {record.check}: {record.summary}"

        for ack in result.revoked_acks:
            log.info("Acknowledgment #%d on %s (%s) revoked: %s", ack.id, ack.activity_id, ack.check, ack.revoked_reason)
        for record in result.new:
            log.info("New issue: %s", label(record))
        for record in result.reopened:
            log.info("Reopened issue (%s): %s", record.reopen_reason, label(record))
        for record in result.resolved:
            log.info("Resolved issue: %s", label(record))
        log.info("Checks done: %s; %d new, %d reopened, %d resolved",
                 ", ".join(f"{k}={v}" for k, v in result.counts.items()) or "none",
                 len(result.new), len(result.reopened), len(result.resolved))
