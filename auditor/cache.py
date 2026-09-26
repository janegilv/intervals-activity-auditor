import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from auditor.models import Activity

class ActivityCache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
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
        self.conn.commit()

    def upsert_many(self, payloads):
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for p in payloads:
            rows.append((
                str(p.get("id")),
                p.get("name", ""),
                p.get("type") or p.get("activity_type") or "",
                p.get("start_date_local"),
                p.get("start_date"),
                p.get("moving_time"),
                p.get("elapsed_time"),
                p.get("distance"),
                json.dumps(p, separators=(",", ":")),
                now,
            ))
        self.conn.executemany("""
        INSERT INTO activities
        (id,name,activity_type,start_date_local,start_date,moving_time,elapsed_time,distance,raw_json,cached_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            activity_type=excluded.activity_type,
            start_date_local=excluded.start_date_local,
            start_date=excluded.start_date,
            moving_time=excluded.moving_time,
            elapsed_time=excluded.elapsed_time,
            distance=excluded.distance,
            raw_json=excluded.raw_json,
            cached_at=excluded.cached_at
        """, rows)
        self.conn.commit()

    def prune_missing(self, current_ids) -> int:
        """Delete cached rows whose id is not in current_ids. Only call this
        with the id set from a --full-sync response (the complete activity
        history from Intervals.icu) -- there, "missing from the fresh
        response" reliably means "deleted upstream". Calling this with an
        incremental/windowed fetch's ids would wrongly delete every activity
        outside that window, so callers must not do that.

        Uses a temp table rather than a SQL "NOT IN (...)" list so this
        works regardless of how many ids there are (sqlite has a parameter
        count limit a plain IN-list would hit on a full history).

        Returns the number of rows deleted.
        """
        self.conn.execute("CREATE TEMP TABLE IF NOT EXISTS _sync_ids (id TEXT PRIMARY KEY)")
        self.conn.execute("DELETE FROM _sync_ids")
        self.conn.executemany(
            "INSERT OR IGNORE INTO _sync_ids (id) VALUES (?)",
            [(str(i),) for i in current_ids],
        )
        cur = self.conn.execute("DELETE FROM activities WHERE id NOT IN (SELECT id FROM _sync_ids)")
        self.conn.commit()
        return cur.rowcount

    def all_activities(self):
        rows = self.conn.execute("SELECT * FROM activities ORDER BY start_date, start_date_local").fetchall()
        return [
            Activity(
                id=r["id"], name=r["name"], activity_type=r["activity_type"],
                start_date_local=r["start_date_local"], start_date=r["start_date"],
                moving_time=r["moving_time"], elapsed_time=r["elapsed_time"],
                distance=r["distance"], raw=json.loads(r["raw_json"]), cached_at=r["cached_at"]
            ) for r in rows
        ]

    def set_state(self, key, value):
        self.conn.execute(
            "INSERT INTO sync_state(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_state(self, key):
        row = self.conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def close(self):
        self.conn.close()
