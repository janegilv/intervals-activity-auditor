"""Data access for each table. Repositories only read and write rows; they
never commit on their own -- callers group writes with
Database.transaction() so a run's changes land together.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from intervals_auditor.db import Database
from intervals_auditor.fingerprint import activity_fingerprint
from intervals_auditor.models import Acknowledgment, Activity, Issue, IssueRecord
from intervals_auditor.streams import Streams
from intervals_auditor.streams import decode as decode_streams, encode as encode_streams

STRAVA_SOURCE = "STRAVA"
STRAVA_STUB_MARKER = "_note"  # present only on Intervals.icu's un-fetched Strava placeholder


def _split_ids(value: str | None) -> tuple[str, ...]:
    return tuple(v for v in (value or "").split("+") if v)


def _join_ids(ids: Iterable[str]) -> str:
    return "+".join(sorted(ids))


@dataclass
class UpsertStats:
    inserted: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: int = 0


class ActivityRepository:
    def __init__(self, db: Database):
        self.conn = db.conn

    @staticmethod
    def _from_row(r) -> Activity:
        return Activity(
            id=r["id"], name=r["name"], activity_type=r["activity_type"],
            start_date_local=r["start_date_local"], start_date=r["start_date"],
            moving_time=r["moving_time"], elapsed_time=r["elapsed_time"],
            distance=r["distance"], raw=json.loads(r["raw_json"]),
            synced_at=r["synced_at"], fingerprint=r["fingerprint"],
            first_seen_at=r["first_seen_at"], updated_at=r["updated_at"],
        )

    def upsert_many(self, payloads: list[dict], now: str) -> UpsertStats:
        """Insert or update activities from API payloads. An activity whose
        fingerprint changed gets a new updated_at; first_seen_at is only set
        when the activity is first stored."""
        existing = dict(self.conn.execute("SELECT id, fingerprint FROM activities").fetchall())
        stats = UpsertStats()
        rows = []
        for p in payloads:
            activity_id = str(p.get("id"))
            fp = activity_fingerprint(p)
            if activity_id not in existing:
                stats.inserted.append(activity_id)
            elif existing[activity_id] != fp:
                stats.changed.append(activity_id)
            else:
                stats.unchanged += 1
            rows.append((
                activity_id,
                p.get("name", ""),
                p.get("type") or p.get("activity_type") or "",
                p.get("start_date_local"),
                p.get("start_date"),
                p.get("moving_time"),
                p.get("elapsed_time"),
                p.get("distance"),
                json.dumps(p, separators=(",", ":")),
                now, fp, now, now,
            ))
        self.conn.executemany("""
        INSERT INTO activities
            (id, name, activity_type, start_date_local, start_date, moving_time, elapsed_time,
             distance, raw_json, synced_at, fingerprint, first_seen_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            activity_type=excluded.activity_type,
            start_date_local=excluded.start_date_local,
            start_date=excluded.start_date,
            moving_time=excluded.moving_time,
            elapsed_time=excluded.elapsed_time,
            distance=excluded.distance,
            raw_json=excluded.raw_json,
            synced_at=excluded.synced_at,
            updated_at=CASE WHEN activities.fingerprint IS excluded.fingerprint
                            THEN activities.updated_at ELSE excluded.updated_at END,
            fingerprint=excluded.fingerprint
        """, rows)
        return stats

    def all(self) -> list[Activity]:
        rows = self.conn.execute(
            "SELECT * FROM activities ORDER BY start_date, start_date_local"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def get(self, activity_id: str) -> Activity | None:
        row = self.conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
        return self._from_row(row) if row else None

    def get_many(self, ids: Iterable[str]) -> dict[str, Activity]:
        result = {}
        for activity_id in set(ids):
            activity = self.get(activity_id)
            if activity:
                result[activity_id] = activity
        return result

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]

    def all_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT id FROM activities")}

    def ids_with_local_date_between(self, first: date, last: date) -> set[str]:
        """Activities whose local start date is within [first, last]."""
        rows = self.conn.execute(
            "SELECT id FROM activities "
            "WHERE substr(COALESCE(start_date_local, start_date), 1, 10) BETWEEN ? AND ?",
            (first.isoformat(), last.isoformat()),
        )
        return {r[0] for r in rows}

    def delete_many(self, ids: Iterable[str]) -> list[Activity]:
        """Delete activities, returning what was deleted (for logging)."""
        deleted = []
        for activity_id in sorted(set(ids)):
            activity = self.get(activity_id)
            if activity:
                self.conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
                deleted.append(activity)
        return deleted

    def _strava_sourced(self) -> list[Activity]:
        # Cheap text prefilter, then an exact check on the parsed payload.
        rows = self.conn.execute(
            "SELECT * FROM activities WHERE raw_json LIKE '%\"source\":\"STRAVA\"%'"
        ).fetchall()
        return [a for a in map(self._from_row, rows) if a.raw.get("source") == STRAVA_SOURCE]

    def unresolved_strava_stubs(self) -> list[Activity]:
        """Intervals.icu placeholders for Strava-only activities that haven't
        been looked up on Strava yet."""
        return [a for a in self._strava_sourced() if STRAVA_STUB_MARKER in a.raw]

    def resolved_strava_ids(self) -> set[str]:
        """Strava-sourced activities already looked up on Strava (enriched
        or confirmed not found)."""
        return {a.id for a in self._strava_sourced() if "_strava_lookup" in a.raw}

    def resolved_strava_records(self) -> list[dict]:
        return [a.raw for a in self._strava_sourced() if "_strava_lookup" in a.raw]


class IssueRepository:
    def __init__(self, db: Database):
        self.conn = db.conn

    @staticmethod
    def _from_row(r) -> IssueRecord:
        return IssueRecord(
            key=r["issue_key"], activity_id=r["activity_id"], check=r["check_name"],
            related_activity_ids=_split_ids(r["related_activity_ids"]),
            severity=r["severity"], summary=r["summary"], details=json.loads(r["details_json"]),
            status=r["status"], first_seen_at=r["first_seen_at"], last_seen_at=r["last_seen_at"],
            resolved_at=r["resolved_at"], notified_at=r["notified_at"],
            open_count=r["open_count"], reopen_reason=r["reopen_reason"], number=r["number"],
        )

    def list(self, *, status: str | None = None, activity_id: str | None = None,
             checks: Iterable[str] | None = None) -> list[IssueRecord]:
        sql, params = "SELECT * FROM issues WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if activity_id:
            sql += " AND activity_id = ?"
            params.append(activity_id)
        if checks is not None:
            checks = list(checks)
            sql += f" AND check_name IN ({','.join('?' * len(checks))})"
            params.extend(checks)
        return [self._from_row(r) for r in self.conn.execute(sql + " ORDER BY first_seen_at", params)]

    def get(self, key: str) -> IssueRecord | None:
        row = self.conn.execute("SELECT * FROM issues WHERE issue_key = ?", (key,)).fetchone()
        return self._from_row(row) if row else None

    def get_by_number(self, number: int) -> IssueRecord | None:
        row = self.conn.execute("SELECT * FROM issues WHERE number = ?", (number,)).fetchone()
        return self._from_row(row) if row else None

    def insert(self, issue: Issue, now: str) -> None:
        self.conn.execute("""
        INSERT INTO issues (issue_key, activity_id, check_name, related_activity_ids, severity,
                            summary, details_json, status, first_seen_at, last_seen_at, number)
        VALUES (?,?,?,?,?,?,?,'open',?,?, (SELECT COALESCE(MAX(number), 0) + 1 FROM issues))
        """, (issue.key, issue.activity_id, issue.check, _join_ids(issue.related_activity_ids),
              issue.severity, issue.summary, json.dumps(issue.details, default=str), now, now))

    def refresh(self, issue: Issue, now: str) -> None:
        """Still open: update what the check said about it this time."""
        self.conn.execute("""
        UPDATE issues SET severity = ?, summary = ?, details_json = ?, last_seen_at = ?
        WHERE issue_key = ?
        """, (issue.severity, issue.summary, json.dumps(issue.details, default=str), now, issue.key))

    def reopen(self, issue: Issue, now: str, reason: str) -> None:
        """A resolved issue is found again."""
        self.conn.execute("""
        UPDATE issues SET status = 'open', severity = ?, summary = ?, details_json = ?,
               first_seen_at = ?, last_seen_at = ?, resolved_at = NULL, notified_at = NULL,
               open_count = open_count + 1, reopen_reason = ?
        WHERE issue_key = ?
        """, (issue.severity, issue.summary, json.dumps(issue.details, default=str),
              now, now, reason, issue.key))

    def restart_notification(self, key: str, now: str, reason: str) -> None:
        """An open issue should be notified about again (e.g. its
        acknowledgment was revoked because the activity changed)."""
        self.conn.execute("""
        UPDATE issues SET first_seen_at = ?, notified_at = NULL, reopen_reason = ?
        WHERE issue_key = ?
        """, (now, reason, key))

    def resolve(self, key: str, now: str) -> None:
        self.conn.execute(
            "UPDATE issues SET status = 'resolved', resolved_at = ? WHERE issue_key = ?", (now, key)
        )

    def mark_notified(self, keys: Iterable[str], now: str) -> None:
        self.conn.executemany(
            "UPDATE issues SET notified_at = ? WHERE issue_key = ?", [(now, k) for k in keys]
        )

    def counts_by_status(self) -> dict[str, int]:
        return dict(self.conn.execute("SELECT status, COUNT(*) FROM issues GROUP BY status").fetchall())


class AcknowledgmentRepository:
    def __init__(self, db: Database):
        self.conn = db.conn

    @staticmethod
    def _from_row(r) -> Acknowledgment:
        return Acknowledgment(
            id=r["id"], issue_key=r["issue_key"], activity_id=r["activity_id"],
            check=r["check_name"], related_activity_ids=_split_ids(r["related_activity_ids"]),
            note=r["note"], fingerprints=json.loads(r["fingerprints_json"]),
            snapshot=json.loads(r["snapshot_json"]), created_at=r["created_at"],
            revoked_at=r["revoked_at"], revoked_reason=r["revoked_reason"],
            issue_number=r["issue_number"],
        )

    # Acknowledgments are shown with the number of the issue they cover.
    _SELECT = ("SELECT a.*, i.number AS issue_number FROM acknowledgments a "
               "LEFT JOIN issues i ON i.issue_key = a.issue_key")

    def create(self, *, issue: IssueRecord, note: str, fingerprints: dict, snapshot: dict,
               now: str) -> Acknowledgment:
        cur = self.conn.execute("""
        INSERT INTO acknowledgments (issue_key, activity_id, check_name, related_activity_ids, note,
                                     fingerprints_json, snapshot_json, created_at)
        VALUES (?,?,?,?,?,?,?,?)
        """, (issue.key, issue.activity_id, issue.check, _join_ids(issue.related_activity_ids),
              note, json.dumps(fingerprints), json.dumps(snapshot, default=str), now))
        return self.get(cur.lastrowid)

    def get(self, ack_id: int) -> Acknowledgment | None:
        row = self.conn.execute(f"{self._SELECT} WHERE a.id = ?", (ack_id,)).fetchone()
        return self._from_row(row) if row else None

    def list(self, *, include_revoked: bool = False, activity_id: str | None = None) -> list[Acknowledgment]:
        sql, params = f"{self._SELECT} WHERE 1=1", []
        if not include_revoked:
            sql += " AND a.revoked_at IS NULL"
        if activity_id:
            # The activity itself, or one of the related activities.
            sql += " AND (a.activity_id = ? OR instr('+' || a.related_activity_ids || '+', ?) > 0)"
            params += [activity_id, f"+{activity_id}+"]
        return [self._from_row(r) for r in self.conn.execute(sql + " ORDER BY a.created_at, a.id", params)]

    def active_by_issue_key(self) -> dict[str, Acknowledgment]:
        return {a.issue_key: a for a in self.list()}

    def revoke(self, ack_id: int, now: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE acknowledgments SET revoked_at = ?, revoked_reason = ? WHERE id = ?",
            (now, reason, ack_id),
        )


class CheckSettingRepository:
    """Check settings changed in the web interface (see services/check_settings.py)."""

    def __init__(self, db: Database):
        self.conn = db.conn

    def all(self) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for r in self.conn.execute("SELECT check_name, key, value_json FROM check_settings ORDER BY check_name, key"):
            result.setdefault(r["check_name"], {})[r["key"]] = json.loads(r["value_json"])
        return result

    def set(self, check_name: str, key: str, value, now: str) -> None:
        self.conn.execute(
            "INSERT INTO check_settings (check_name, key, value_json, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(check_name, key) DO UPDATE SET value_json = excluded.value_json, "
            "updated_at = excluded.updated_at",
            (check_name, key, json.dumps(value), now),
        )

    def delete(self, check_name: str, key: str | None = None) -> None:
        if key is None:
            self.conn.execute("DELETE FROM check_settings WHERE check_name = ?", (check_name,))
        else:
            self.conn.execute("DELETE FROM check_settings WHERE check_name = ? AND key = ?", (check_name, key))


@dataclass
class StreamRecord:
    activity_id: str
    version: str
    status: str  # ok | unavailable | error
    fetched_at: str
    point_count: int = 0
    error: str | None = None


class StreamRepository:
    """Stored activity streams (see streams.py)."""

    def __init__(self, db: Database):
        self.conn = db.conn

    def records(self) -> dict[str, StreamRecord]:
        """What's stored for each activity, without the data itself."""
        rows = self.conn.execute(
            "SELECT activity_id, version, status, fetched_at, point_count, error FROM activity_streams")
        return {r["activity_id"]: StreamRecord(r["activity_id"], r["version"], r["status"], r["fetched_at"],
                                               r["point_count"], r["error"]) for r in rows}

    def get(self, activity_id: str, version: str | None = None) -> Streams | None:
        """The activity's streams, if stored (and fetched from `version`)."""
        row = self.conn.execute(
            "SELECT version, data FROM activity_streams WHERE activity_id = ? AND status = 'ok'",
            (activity_id,)).fetchone()
        if row is None or row["data"] is None or (version is not None and row["version"] != version):
            return None
        return decode_streams(row["data"])

    def save(self, activity_id: str, version: str, now: str, streams: Streams | None, *,
             status: str | None = None, error: str | None = None) -> None:
        status = status or ("ok" if streams is not None else "unavailable")
        self.conn.execute("""
        INSERT INTO activity_streams (activity_id, version, status, fetched_at, point_count, data, error)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(activity_id) DO UPDATE SET
            version=excluded.version, status=excluded.status, fetched_at=excluded.fetched_at,
            point_count=excluded.point_count, data=excluded.data, error=excluded.error
        """, (activity_id, version, status, now, len(streams) if streams else 0,
              encode_streams(streams) if streams else None, error))

    def record_error(self, activity_id: str, version: str, now: str, error: str) -> None:
        """A fetch failed: remember why (so it's tried again), but keep any
        streams stored from an earlier version of the activity."""
        self.conn.execute("""
        INSERT INTO activity_streams (activity_id, version, status, fetched_at, point_count, data, error)
        VALUES (?,?,'error',?,0,NULL,?)
        ON CONFLICT(activity_id) DO UPDATE SET error=excluded.error
        """, (activity_id, version, now, error))

    def counts(self) -> dict[str, int]:
        return dict(self.conn.execute("SELECT status, COUNT(*) FROM activity_streams GROUP BY status").fetchall())


class StateRepository:
    """Small key/value store for sync bookkeeping (last sync times, health)."""

    def __init__(self, db: Database):
        self.conn = db.conn

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO sync_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM sync_state WHERE key = ?", (key,))


@dataclass
class RunRecord:
    id: int
    command: str
    started_at: str
    finished_at: str | None
    status: str
    summary: dict
    error: str | None


class RunRepository:
    """One row per sync/monitor run, so it's easy to see what happened when."""

    def __init__(self, db: Database):
        self.conn = db.conn

    def start(self, command: str, now: str) -> int:
        return self.conn.execute(
            "INSERT INTO runs (command, started_at) VALUES (?, ?)", (command, now)
        ).lastrowid

    def finish(self, run_id: int, now: str, status: str, summary: dict | None = None,
               error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at = ?, status = ?, summary_json = ?, error = ? WHERE id = ?",
            (now, status, json.dumps(summary or {}, default=str), error, run_id),
        )

    def recent(self, limit: int = 10) -> list[RunRecord]:
        rows = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [
            RunRecord(id=r["id"], command=r["command"], started_at=r["started_at"],
                      finished_at=r["finished_at"], status=r["status"],
                      summary=json.loads(r["summary_json"] or "{}"), error=r["error"])
            for r in rows
        ]
