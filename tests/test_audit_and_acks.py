import unittest
from datetime import timedelta

from intervals_auditor.services.acks import AckService, NotFoundError
from intervals_auditor.services.audit import AuditService, UnknownCheckError
from intervals_auditor.services.queries import QueryService
from intervals_auditor.views import ACKNOWLEDGED, OPEN

from tests.helpers import FakeClock, Repos, payload

CONFIG = {
    "duplicate_overlap": {"enabled": True, "minimum_overlap_percent": 10.0, "require_same_type": False},
    "under_time_limit": {"enabled": True, "maximum_duration_seconds": 100},
    "moving_elapsed_gap": {"enabled": False},
}


class AuditAndAckTest(unittest.TestCase):
    def setUp(self):
        self.r = Repos()
        self.clock = FakeClock()
        self.audit = AuditService(self.r.db, self.r.activities, self.r.issues, self.r.acks, CONFIG, clock=self.clock)
        self.ack = AckService(self.r.db, self.r.activities, self.r.issues, self.r.acks, clock=self.clock)
        self.queries = QueryService(self.r.activities, self.r.issues, self.r.acks, self.r.state, self.r.runs)
        # i1/i2 overlap (duplicate pair), i3 is a 30 second false start.
        self.r.add(payload("i1", start="2026-09-20T07:00:00"),
                   payload("i2", start="2026-09-20T07:00:30"),
                   payload("i3", start="2026-09-21T07:00:00", seconds=30))

    def keys(self, records):
        return sorted(r.key for r in records)

    def test_issue_lifecycle_new_refresh_resolve_reopen(self):
        first = self.audit.run()
        self.assertEqual(self.keys(first.new), ["i1|duplicate_overlap|i2", "i2|duplicate_overlap|i1",
                                                "i3|under_time_limit"])
        self.clock.advance(minutes=30)
        second = self.audit.run()
        self.assertEqual((second.new, second.reopened, second.resolved), ([], [], []))

        # i3 gets fixed (made longer) -> resolved
        self.r.add(payload("i3", start="2026-09-21T07:00:00", seconds=3000))
        third = self.audit.run()
        self.assertEqual(self.keys(third.resolved), ["i3|under_time_limit"])

        # ...and broken again -> reopened, notification state reset
        self.r.add(payload("i3", start="2026-09-21T07:00:00", seconds=30))
        self.r.issues.mark_notified(["i3|under_time_limit"], "x")
        fourth = self.audit.run()
        self.assertEqual(self.keys(fourth.reopened), ["i3|under_time_limit"])
        record = self.r.issues.get("i3|under_time_limit")
        self.assertEqual(record.open_count, 2)
        self.assertIsNone(record.notified_at)

    def test_only_run_leaves_other_checks_alone_and_disabled_checks_resolve(self):
        self.audit.run(only=["moving_elapsed_gap"])  # nothing found, nothing resolved
        self.audit.run()
        result = self.audit.run(only=["under_time_limit"])
        self.assertEqual(result.resolved, [])
        self.assertEqual(len(self.r.issues.list(status="open")), 3)
        with self.assertRaises(UnknownCheckError):
            self.audit.run(only=["nope"])

    def test_ack_pair_hides_both_sides_until_activity_changes(self):
        self.audit.run()
        created = self.ack.acknowledge("i1", note="two real sessions")
        self.assertEqual(sorted(a.issue_key for a in created),
                         ["i1|duplicate_overlap|i2", "i2|duplicate_overlap|i1"])
        self.assertEqual(created[0].snapshot["i1"]["url"], "https://intervals.icu/activities/i1")
        self.assertEqual(self.ack.acknowledge("i1"), [])  # nothing left to acknowledge

        views = {v.activity_id: v for v in self.queries.activity_views(states=(OPEN,))}
        self.assertEqual(list(views), ["i3"])
        acked = self.queries.activity_views(states=(ACKNOWLEDGED,))
        self.assertEqual(sorted(v.activity_id for v in acked), ["i1", "i2"])

        # A rename is not a change...
        self.r.add(payload("i2", start="2026-09-20T07:00:30", name="Renamed"))
        self.assertEqual(self.audit.run().revoked_acks, [])
        # ...but a different duration is: both acks involving i2 are revoked.
        self.r.add(payload("i2", start="2026-09-20T07:00:30", seconds=3500))
        result = self.audit.run()
        self.assertEqual(len(result.revoked_acks), 2)
        self.assertIn("i2 changed", result.revoked_acks[0].revoked_reason)
        self.assertEqual(self.keys(result.reopened), ["i1|duplicate_overlap|i2", "i2|duplicate_overlap|i1"])
        self.assertIn("acknowledgment revoked", self.r.issues.get("i1|duplicate_overlap|i2").reopen_reason)
        self.assertEqual(len(self.ack.list(include_revoked=True)), 2)

    def test_ack_single_side_and_unack(self):
        self.audit.run()
        created = self.ack.acknowledge("i1", include_related=False)
        self.assertEqual([a.issue_key for a in created], ["i1|duplicate_overlap|i2"])
        self.ack.acknowledge("i2", check="duplicate_overlap")
        self.assertEqual(len(self.ack.list()), 2)
        revoked = self.ack.unacknowledge("i2")  # i2's own ack and i1's, which points at i2
        self.assertEqual(len(revoked), 2)
        self.assertEqual(self.ack.list(), [])
        with self.assertRaises(NotFoundError):
            self.ack.acknowledge("missing")

    def test_issue_numbers_are_stable_and_usable_for_acks(self):
        self.audit.run()
        numbers = {r.key: r.number for r in self.r.issues.list()}
        self.assertEqual(sorted(numbers.values()), [1, 2, 3])
        # resolve and reopen i3's issue: same number
        self.r.add(payload("i3", start="2026-09-21T07:00:00", seconds=3000))
        self.audit.run()
        self.r.add(payload("i3", start="2026-09-21T07:00:00", seconds=30))
        self.audit.run()
        self.assertEqual(self.r.issues.get("i3|under_time_limit").number, numbers["i3|under_time_limit"])

        pair = numbers["i1|duplicate_overlap|i2"]
        created = self.ack.acknowledge_issue(pair, note="both real")
        self.assertEqual(sorted(a.issue_number for a in created), sorted([pair, numbers["i2|duplicate_overlap|i1"]]))
        self.assertEqual(self.ack.unacknowledge_issue(pair, include_related=False)[0].issue_number, pair)
        self.assertEqual(len(self.ack.list()), 1)  # the mirror stays acknowledged
        self.assertEqual(self.queries.resolve_ref(f"#{pair}"), ("issue", pair))
        self.assertEqual(self.queries.resolve_ref("1"), ("activity", "i1"))  # activity ids win...
        self.assertEqual(self.queries.resolve_ref("#1"), ("issue", 1))       # ...unless written as #1
        with self.assertRaises(NotFoundError):
            self.ack.acknowledge_issue(99)

    def test_deleted_activity_resolves_issue_and_revokes_ack(self):
        self.audit.run()
        self.ack.acknowledge("i3", note="false start")
        with self.r.db.transaction():
            self.r.activities.delete_many(["i3"])
        result = self.audit.run()
        self.assertEqual(self.keys(result.resolved), ["i3|under_time_limit"])
        self.assertIn("deleted", result.revoked_acks[0].revoked_reason)
        detail = self.queries.activity_detail("i3")
        self.assertIsNone(detail.activity)
        self.assertEqual(detail.acks[0].snapshot["i3"]["duration_seconds"], 30)

    def test_notifiable_respects_waiting_period(self):
        self.audit.run()
        ready, waiting = self.queries.notifiable(self.clock() - timedelta(minutes=60))
        self.assertEqual((ready, waiting), ([], 3))
        self.clock.advance(minutes=61)
        ready, waiting = self.queries.notifiable(self.clock() - timedelta(minutes=60))
        self.assertEqual(sorted(v.activity_id for v in ready), ["i1", "i2", "i3"])
        self.assertEqual(waiting, 0)


if __name__ == "__main__":
    unittest.main()
