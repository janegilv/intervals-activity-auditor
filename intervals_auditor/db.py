"""SQLite database: connection handling, transactions and schema migrations.

Everything the application knows lives in this one file -- activities
(including data fetched from Strava), the issues found on them, your
acknowledgments, and a log of runs. It's the application's own store, not
a disposable cache: don't delete it to "refresh" data, deletions upstream
are picked up by syncing.

Schema changes are numbered migrations tracked with SQLite's PRAGMA
user_version: to change the schema, append a function to MIGRATIONS.
Existing databases run only the migrations they haven't had yet.
"""
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

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

def _migration_1(conn: sqlite3.Connection) -> None:
    """The initial schema."""
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
        synced_at TEXT NOT NULL,
        fingerprint TEXT,
        first_seen_at TEXT,
        updated_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_activities_start_local ON activities(start_date_local);

    CREATE TABLE IF NOT EXISTS sync_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

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


def _migration_2(conn: sqlite3.Connection) -> None:
    """Issue numbers: a short, stable number per issue to type in commands
    (`ack 12`). Existing issues are numbered in the order they were found."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(issues)")}
    if "number" not in columns:
        conn.execute("ALTER TABLE issues ADD COLUMN number INTEGER")
    start = conn.execute("SELECT COALESCE(MAX(number), 0) FROM issues").fetchone()[0]
    keys = [r[0] for r in conn.execute(
        "SELECT issue_key FROM issues WHERE number IS NULL ORDER BY first_seen_at, rowid")]
    conn.executemany("UPDATE issues SET number = ? WHERE issue_key = ?",
                     [(start + i, key) for i, key in enumerate(keys, 1)])
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_issues_number ON issues(number)")


MIGRATIONS = [_migration_1, _migration_2]
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
