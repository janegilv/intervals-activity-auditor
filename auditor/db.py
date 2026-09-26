"""SQLite database: connection handling, transactions and schema migrations.

Everything the application knows lives in this one file -- activities
(including data fetched from Strava), the issues found on them, your
acknowledgments, and a log of runs. It's the application's own store, not
a disposable cache: don't delete it to "refresh" data, deletions upstream
are picked up by syncing.

Migrations are numbered and tracked with SQLite's PRAGMA user_version. A
database created by the earlier cache-only version of this tool (tables
activities + sync_state, user_version 0) is upgraded in place.
"""
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from auditor.fingerprint import activity_fingerprint

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if isinstance(self.path, Path):
            # WAL lets a future web interface read while a sync is writing.
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._depth = 0
        migrate(self.conn)

    @contextmanager
    def transaction(self):
        """Group writes so they're committed together (or not at all).
        Nested use joins the outermost transaction."""
        self._depth += 1
        try:
            yield self.conn
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.rollback()
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.conn.commit()

    def close(self):
        self.conn.close()


# --- migrations -----------------------------------------------------------

def _columns(conn, table) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migration_1(conn: sqlite3.Connection) -> None:
    """Upgrade the cache-only schema (or create from scratch) to the
    activity/issue/acknowledgment schema."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS activities (
        id TEXT PRIMARY KEY,
        name TEXT,
        activity_type TEXT,
        start_date_local TEXT,
        start_date TEXT,
        moving_time INTEGER,
        elapsed_time INTEGER,
        distance REAL,
        raw_json TEXT NOT NULL,
        cached_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sync_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    cols = _columns(conn, "activities")
    if "cached_at" in cols and "synced_at" not in cols:
        conn.execute("ALTER TABLE activities RENAME COLUMN cached_at TO synced_at")
    for column in ("fingerprint", "first_seen_at", "updated_at"):
        if column not in _columns(conn, "activities"):
            conn.execute(f"ALTER TABLE activities ADD COLUMN {column} TEXT")

    rows = conn.execute(
        "SELECT id, raw_json, synced_at FROM activities WHERE fingerprint IS NULL"
    ).fetchall()
    conn.executemany(
        "UPDATE activities SET fingerprint = ?, first_seen_at = COALESCE(first_seen_at, ?), "
        "updated_at = COALESCE(updated_at, ?) WHERE id = ?",
        [
            (activity_fingerprint(json.loads(r["raw_json"])), r["synced_at"], r["synced_at"], r["id"])
            for r in rows
        ],
    )
    if rows:
        log.info("Database upgrade: fingerprinted %d existing activities", len(rows))

    conn.executescript("""
    CREATE INDEX IF NOT EXISTS idx_activities_start_local ON activities(start_date_local);

    CREATE TABLE IF NOT EXISTS issues (
        issue_key TEXT PRIMARY KEY,
        activity_id TEXT NOT NULL,
        check_name TEXT NOT NULL,
        related_activity_ids TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL,
        summary TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        resolved_at TEXT,
        notified_at TEXT,
        open_count INTEGER NOT NULL DEFAULT 1,
        reopen_reason TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_issues_activity ON issues(activity_id);
    CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);

    CREATE TABLE IF NOT EXISTS acknowledgments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_key TEXT NOT NULL,
        activity_id TEXT NOT NULL,
        check_name TEXT NOT NULL,
        related_activity_ids TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        fingerprints_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        revoked_at TEXT,
        revoked_reason TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_acks_one_active_per_issue
        ON acknowledgments(issue_key) WHERE revoked_at IS NULL;
    CREATE INDEX IF NOT EXISTS idx_acks_activity ON acknowledgments(activity_id);

    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        command TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running',
        summary_json TEXT,
        error TEXT
    );
    """)


MIGRATIONS = [_migration_1]
SCHEMA_VERSION = len(MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than this code supports "
            f"({SCHEMA_VERSION}). Update the application."
        )
    for number in range(version + 1, SCHEMA_VERSION + 1):
        log.info("Upgrading database schema to version %d", number)
        try:
            MIGRATIONS[number - 1](conn)
            conn.execute(f"PRAGMA user_version = {number}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def copy_database(source: Path, target: Path) -> None:
    """Consistent copy of a SQLite file (safe even if it's in use)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
