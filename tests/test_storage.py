import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from intervals_auditor.db import SCHEMA_VERSION, Database
from intervals_auditor.fingerprint import activity_fingerprint
from intervals_auditor.models import Issue
from intervals_auditor.repositories import ActivityRepository

from tests.helpers import Repos, payload


class FingerprintTest(unittest.TestCase):
    def test_ignores_name_and_analysis_fields(self):
        base = payload("i1")
        renamed = dict(base, name="Renamed", description="x", icu_training_load=42)
        self.assertEqual(activity_fingerprint(base), activity_fingerprint(renamed))

    def test_changes_with_timing_distance_type_and_links(self):
        base = payload("i1")
        for change in ({"elapsed_time": 3000}, {"distance": 25000.0}, {"type": "Run"},
                       {"strava_id": None}, {"start_date": "2026-09-20T06:00:00Z"}):
            self.assertNotEqual(activity_fingerprint(base), activity_fingerprint(dict(base, **change)), change)

    def test_small_distance_wobble_is_ignored(self):
        self.assertEqual(activity_fingerprint(payload("i1", distance=30001.0)),
                         activity_fingerprint(payload("i1", distance=30003.9)))


class LegacyMigrationTest(unittest.TestCase):
    def test_upgrades_cache_schema_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.sqlite"
            conn = sqlite3.connect(path)
            conn.executescript("""
            CREATE TABLE activities (id TEXT PRIMARY KEY, name TEXT, activity_type TEXT,
                start_date_local TEXT, start_date TEXT, moving_time INTEGER, elapsed_time INTEGER,
                distance REAL, raw_json TEXT NOT NULL, cached_at TEXT NOT NULL);
            CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            p = payload("i1")
            conn.execute("INSERT INTO activities VALUES (?,?,?,?,?,?,?,?,?,?)",
                         ("i1", p["name"], "Ride", p["start_date_local"], p["start_date"], 3600, 3600,
                          30000.0, json.dumps(p), "2026-01-01T00:00:00+00:00"))
            conn.execute("INSERT INTO sync_state VALUES ('last_successful_sync', 'x')")
            conn.commit()
            conn.close()

            db = Database(path)
            self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            activity = ActivityRepository(db).get("i1")
            self.assertEqual(activity.synced_at, "2026-01-01T00:00:00+00:00")
            self.assertEqual(activity.first_seen_at, "2026-01-01T00:00:00+00:00")
            self.assertEqual(activity.fingerprint, activity_fingerprint(p))
            db.close()
            # Re-opening an upgraded database is a no-op.
            Database(path).close()


class ActivityRepositoryTest(unittest.TestCase):
    def test_upsert_tracks_inserted_changed_unchanged(self):
        r = Repos()
        stats = r.add(payload("i1"), payload("i2"), now="2026-09-20T10:00:00+00:00")
        self.assertEqual(sorted(stats.inserted), ["i1", "i2"])

        stats = r.add(payload("i1", name="renamed"), payload("i2", seconds=100),
                      now="2026-09-21T10:00:00+00:00")
        self.assertEqual(stats.changed, ["i2"])
        self.assertEqual(stats.unchanged, 1)
        i1, i2 = r.activities.get("i1"), r.activities.get("i2")
        self.assertEqual(i1.name, "renamed")
        self.assertEqual(i1.updated_at, "2026-09-20T10:00:00+00:00")  # rename isn't a change
        self.assertEqual(i2.updated_at, "2026-09-21T10:00:00+00:00")
        self.assertEqual(i2.first_seen_at, "2026-09-20T10:00:00+00:00")

    def test_local_date_range_and_delete(self):
        r = Repos()
        r.add(payload("i1", start="2026-09-01T08:00:00"), payload("i2", start="2026-09-20T08:00:00"))
        self.assertEqual(r.activities.ids_with_local_date_between(date(2026, 9, 10), date(2026, 9, 30)), {"i2"})
        with r.db.transaction():
            deleted = r.activities.delete_many(["i2", "missing"])
        self.assertEqual([a.id for a in deleted], ["i2"])
        self.assertEqual(r.activities.all_ids(), {"i1"})

    def test_strava_stub_queries(self):
        r = Repos()
        r.add(payload("111", source="STRAVA", _note="stub"),
              payload("222", source="STRAVA", _strava_lookup="enriched"),
              payload("i3"))
        self.assertEqual([a.id for a in r.activities.unresolved_strava_stubs()], ["111"])
        self.assertEqual(r.activities.resolved_strava_ids(), {"222"})


class AcknowledgmentRepositoryTest(unittest.TestCase):
    def test_only_one_active_ack_per_issue_and_activity_filter(self):
        r = Repos()
        issue = Issue("i1", "duplicate_overlap", "high", "dup", ("i2",))
        with r.db.transaction():
            r.issues.insert(issue, "2026-09-20T10:00:00+00:00")
        record = r.issues.get(issue.key)
        with r.db.transaction():
            ack = r.acks.create(issue=record, note="fine", fingerprints={}, snapshot={}, now="t")
        with self.assertRaises(sqlite3.IntegrityError):
            with r.db.transaction():
                r.acks.create(issue=record, note="again", fingerprints={}, snapshot={}, now="t")
        with r.db.transaction():
            r.acks.revoke(ack.id, "t2", "unacknowledged")
            r.acks.create(issue=record, note="again", fingerprints={}, snapshot={}, now="t3")
        self.assertEqual(len(r.acks.list()), 1)
        self.assertEqual(len(r.acks.list(include_revoked=True)), 2)
        self.assertEqual(len(r.acks.list(activity_id="i2")), 1)   # related activity
        self.assertEqual(len(r.acks.list(activity_id="i22")), 0)  # no partial id match


if __name__ == "__main__":
    unittest.main()
