"""Acknowledging issues: "I've looked at this, it's fine".

An acknowledgment covers one issue on one activity. It records a
fingerprint and a snapshot of every activity involved (the activity and,
for checks like duplicate_overlap, the related activities), so that

- the issue stays hidden only while those activities are unchanged -- any
  change revokes the acknowledgment and reopens the issue (see
  services/audit.py), and
- you can still see what was acknowledged, with links, even after an
  activity has been changed or deleted.

Acknowledgments are never deleted: unacknowledging or revoking keeps the
row with revoked_at/revoked_reason set, as history.
"""
import logging

from intervals_auditor.db import Database
from intervals_auditor.models import Acknowledgment, IssueRecord
from intervals_auditor.repositories import AcknowledgmentRepository, ActivityRepository, IssueRepository
from intervals_auditor.timeutil import to_iso

log = logging.getLogger(__name__)

UNACKNOWLEDGED = "unacknowledged"


class NotFoundError(LookupError):
    pass


class AckService:
    def __init__(self, db: Database, activities: ActivityRepository, issues: IssueRepository,
                 acks: AcknowledgmentRepository, *, clock):
        self.db = db
        self.activities = activities
        self.issues = issues
        self.acks = acks
        self.clock = clock

    def _mirrors(self, issue: IssueRecord) -> list[IssueRecord]:
        """The same situation seen from the related activities' side (e.g.
        the other half of a duplicate pair)."""
        mirrors = []
        for related_id in issue.related_activity_ids:
            for other in self.issues.list(status="open", activity_id=related_id, checks=[issue.check]):
                if issue.activity_id in other.related_activity_ids:
                    mirrors.append(other)
        return mirrors

    def acknowledge(self, activity_id: str, *, check: str | None = None, note: str = "",
                    include_related: bool = True) -> list[Acknowledgment]:
        """Acknowledge the open issues on an activity (optionally just one
        check's). With include_related, the mirrored issues on related
        activities are acknowledged too, with the same note."""
        if self.activities.get(activity_id) is None:
            raise NotFoundError(f"No activity {activity_id!r} in the database.")
        active = self.acks.active_by_issue_key()
        targets: dict[str, IssueRecord] = {}
        for issue in self.issues.list(status="open", activity_id=activity_id,
                                      checks=[check] if check else None):
            candidates = [issue] + (self._mirrors(issue) if include_related else [])
            for candidate in candidates:
                if candidate.key not in active:
                    targets.setdefault(candidate.key, candidate)

        now = to_iso(self.clock())
        created = []
        with self.db.transaction():
            for issue in targets.values():
                involved = self.activities.get_many((issue.activity_id, *issue.related_activity_ids))
                ack = self.acks.create(
                    issue=issue,
                    note=note,
                    fingerprints={aid: (involved[aid].fingerprint if aid in involved else None)
                                  for aid in (issue.activity_id, *issue.related_activity_ids)},
                    snapshot={aid: a.snapshot() for aid, a in involved.items()},
                    now=now,
                )
                created.append(ack)
        for ack in created:
            log.info("Acknowledged #%d: %s %s%s%s", ack.id, ack.activity_id, ack.check,
                     f" (with {'+'.join(ack.related_activity_ids)})" if ack.related_activity_ids else "",
                     f" -- {note}" if note else "")
        return created

    def unacknowledge(self, activity_id: str, *, check: str | None = None,
                      include_related: bool = True) -> list[Acknowledgment]:
        """Revoke active acknowledgments on an activity (and, with
        include_related, those on related activities that point back at it)."""
        selected = [
            ack for ack in self.acks.list(activity_id=activity_id)
            if (check is None or ack.check == check)
            and (include_related or ack.activity_id == activity_id)
        ]
        now = to_iso(self.clock())
        with self.db.transaction():
            for ack in selected:
                self.acks.revoke(ack.id, now, UNACKNOWLEDGED)
                ack.revoked_at, ack.revoked_reason = now, UNACKNOWLEDGED
        for ack in selected:
            log.info("Unacknowledged #%d: %s %s", ack.id, ack.activity_id, ack.check)
        return selected

    def list(self, *, include_revoked: bool = False, activity_id: str | None = None) -> list[Acknowledgment]:
        return self.acks.list(include_revoked=include_revoked, activity_id=activity_id)
