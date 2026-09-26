import unittest
from datetime import date

from intervals_auditor.services.strava import enrich_strava_stubs
from intervals_auditor.services.sync import STATE_LAST_FULL_SYNC, SyncService
from intervals_auditor.sources.strava import StravaRateLimitError

from tests.helpers import FakeClock, Repos, payload


class FakeIntervals:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def list_activities(self, oldest, newest):
        self.calls.append((oldest, newest))
        return [p for p in self.payloads if oldest.isoformat() <= p["start_date_local"][:10] <= newest.isoformat()]


class SyncServiceTest(unittest.TestCase):
    def setUp(self):
        self.r = Repos()
        self.clock = FakeClock("2026-09-26T10:00:00+00:00")

    def service(self, client):
        return SyncService(self.r.db, self.r.activities, self.r.state, client,
                           refresh_days=30, full_sync_interval_hours=24, clock=self.clock)

    def test_first_sync_is_full_then_windowed_until_due(self):
        client = FakeIntervals([payload("i1", start="2020-01-01T08:00:00"),
                                payload("i2", start="2026-09-25T08:00:00")])
        svc = self.service(client)
        first = svc.sync()
        self.assertTrue(first.full)
        self.assertEqual(sorted(first.inserted), ["i1", "i2"])
        self.clock.advance(hours=1)
        second = svc.sync()
        self.assertFalse(second.full)
        self.assertEqual(client.calls[-1][0], date(2026, 8, 27))
        self.clock.advance(hours=24)
        self.assertTrue(svc.sync().full)

    def test_window_sync_removes_recent_deleted_activities_only(self):
        self.r.add(payload("old", start="2026-01-01T08:00:00"),
                   payload("keep", start="2026-09-20T08:00:00"),
                   payload("gone", start="2026-09-21T08:00:00"))
        self.r.state.set(STATE_LAST_FULL_SYNC, "2026-09-26T09:00:00+00:00")
        result = self.service(FakeIntervals([payload("keep", start="2026-09-20T08:00:00")])).sync()
        self.assertFalse(result.full)
        self.assertEqual([a.id for a in result.removed], ["gone"])
        self.assertEqual(self.r.activities.all_ids(), {"old", "keep"})

    def test_full_sync_removes_old_deleted_activities(self):
        self.r.add(payload("old", start="2026-01-01T08:00:00"), payload("keep", start="2026-09-20T08:00:00"))
        result = self.service(FakeIntervals([payload("keep", start="2026-09-20T08:00:00")])).sync(full=True)
        self.assertEqual([a.id for a in result.removed], ["old"])

    def test_prune_guard_and_force(self):
        self.r.add(*[payload(f"i{n}", start=f"2026-09-{n + 1:02d}T08:00:00") for n in range(20)])
        result = self.service(FakeIntervals([])).sync(full=True)
        self.assertIsNotNone(result.prune_skipped)
        self.assertEqual(self.r.activities.count(), 20)
        result = self.service(FakeIntervals([])).sync(full=True, force_prune=True)
        self.assertEqual(len(result.removed), 20)

    def test_resolved_strava_record_not_overwritten_by_placeholder(self):
        self.r.add(payload("999", start="2026-09-20T08:00:00", source="STRAVA", _strava_lookup="enriched",
                           name="From Strava"))
        placeholder = {"id": "999", "start_date_local": "2026-09-20T08:00:00", "source": "STRAVA", "_note": "stub"}
        result = self.service(FakeIntervals([placeholder])).sync(full=True)
        self.assertEqual(result.preserved, 1)
        self.assertEqual(self.r.activities.get("999").name, "From Strava")
        self.assertEqual(result.removed, [])


class FakeStrava:
    def __init__(self, responses):
        self.responses = responses

    def get_activity(self, activity_id):
        value = self.responses[activity_id]
        if isinstance(value, Exception):
            raise value
        return value


class StravaEnrichTest(unittest.TestCase):
    def test_enrich_not_found_and_rate_limit(self):
        r = Repos()
        r.add(payload("1", source="STRAVA", _note="stub", start="2026-09-01T08:00:00"),
              payload("2", source="STRAVA", _note="stub", start="2026-09-02T08:00:00"),
              payload("3", source="STRAVA", _note="stub", start="2026-09-03T08:00:00"))
        client = FakeStrava({
            "1": {"name": "Morning Run", "type": "Run", "moving_time": 1800, "elapsed_time": 1900},
            "2": None,
            "3": StravaRateLimitError(retry_after=900),
        })
        result = enrich_strava_stubs(r.db, r.activities, client, clock=FakeClock())
        self.assertEqual(result.enriched, ["1"])
        self.assertEqual(result.not_found, ["2"])
        self.assertTrue(result.rate_limited)
        self.assertEqual([a.id for a in r.activities.unresolved_strava_stubs()], ["3"])
        self.assertEqual(r.activities.get("1").activity_type, "Run")
        self.assertEqual(r.activities.resolved_strava_ids(), {"1", "2"})


if __name__ == "__main__":
    unittest.main()
