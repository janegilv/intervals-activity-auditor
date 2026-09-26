import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import requests

from intervals_auditor.notifiers.base import Notifier, NotifierError
from intervals_auditor.services.acks import AckService
from intervals_auditor.services.audit import AuditService
from intervals_auditor.services.monitor import MonitorService
from intervals_auditor.services.queries import QueryService
from intervals_auditor.services.runner import RunService
from intervals_auditor.services.sync import SyncService

from tests.helpers import FakeClock, Repos, payload

CONFIG = {"under_time_limit": {"enabled": True, "maximum_duration_seconds": 100}}


class FakeIntervals:
    def __init__(self, payloads):
        self.payloads = payloads
        self.fail = False

    def list_activities(self, oldest, newest):
        if self.fail:
            raise requests.ConnectionError("network down")
        return list(self.payloads)


class RecordingNotifier(Notifier):
    name = "recording"

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send(self, notification):
        if self.fail:
            raise NotifierError("boom")
        self.sent.append(notification)


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.r = Repos()
        self.clock = FakeClock("2026-09-26T10:00:00+00:00")
        self.intervals = FakeIntervals([payload("i1", start="2026-09-25T08:00:00"),
                                        payload("i2", start="2026-09-25T12:00:00", seconds=30)])
        self.notifier = RecordingNotifier()
        r = self.r
        self.queries = QueryService(r.activities, r.issues, r.acks, r.state, r.runs)
        audit = AuditService(r.db, r.activities, r.issues, r.acks, CONFIG, clock=self.clock)
        self.runner = RunService(
            db=r.db, activities=r.activities, runs=r.runs, state=r.state, audit=audit, queries=self.queries,
            sync_factory=lambda: SyncService(r.db, r.activities, r.state, self.intervals, clock=self.clock),
            strava_factory=lambda: None, output_dir=Path(self.tmp.name), clock=self.clock)
        self.acks = AckService(r.db, r.activities, r.issues, r.acks, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def monitor(self, notifiers=None):
        return MonitorService(db=self.r.db, issues=self.r.issues, state=self.r.state, queries=self.queries,
                              runner=self.runner, notifiers=[self.notifier] if notifiers is None else notifiers,
                              notify_after_minutes=60, health_alert_after_hours=6, clock=self.clock)

    def test_waits_then_notifies_once(self):
        first = self.monitor().run()
        self.assertEqual((first.waiting, self.notifier.sent), (1, []))
        self.assertTrue((Path(self.tmp.name) / "report.md").exists())

        self.clock.advance(minutes=30)
        self.monitor().run()
        self.assertEqual(self.notifier.sent, [])

        self.clock.advance(minutes=31)
        result = self.monitor().run()
        self.assertEqual(result.notified_issue_count, 1)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertEqual(self.notifier.sent[0].items[0].link_text, "i2")

        self.clock.advance(minutes=30)
        self.monitor().run()
        self.assertEqual(len(self.notifier.sent), 1)  # not repeated

    def test_acknowledged_issue_is_not_notified(self):
        self.monitor().run()
        self.acks.acknowledge("i2", note="false start")
        self.clock.advance(hours=2)
        self.assertEqual(self.monitor().run().notified, [])
        self.assertEqual(self.notifier.sent, [])

    def test_issue_gone_within_waiting_period_is_never_sent(self):
        self.monitor().run()
        self.intervals.payloads[1] = payload("i2", start="2026-09-25T12:00:00", seconds=3000)
        self.clock.advance(minutes=30)
        self.monitor().run()
        self.clock.advance(hours=2)
        self.monitor().run()
        self.assertEqual(self.notifier.sent, [])

    def test_dry_run_and_failed_notifiers_do_not_mark_notified(self):
        self.monitor().run()
        self.clock.advance(hours=2)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.monitor().run(dry_run=True)
        self.assertIn("i2 https://intervals.icu/activities/i2", out.getvalue())
        self.monitor(notifiers=[RecordingNotifier(fail=True)]).run()
        self.monitor(notifiers=[]).run()
        self.assertIsNone(self.r.issues.get("i2|under_time_limit").notified_at)
        self.monitor().run()
        self.assertIsNotNone(self.r.issues.get("i2|under_time_limit").notified_at)

    def test_health_alert_after_hours_of_failing_sync_then_recovery(self):
        self.monitor().run()
        self.intervals.fail = True
        self.clock.advance(hours=1)
        failing = self.monitor().run()
        self.assertIn("network down", failing.run.sync_error)
        self.assertIsNone(failing.health)
        self.clock.advance(hours=6)
        self.assertEqual(self.monitor().run().health, "alert")
        self.clock.advance(hours=1)
        self.assertIsNone(self.monitor().run().health)  # only once
        self.intervals.fail = False
        self.assertEqual(self.monitor().run().health, "recovered")
        titles = [n.title for n in self.notifier.sent]
        self.assertIn("Intervals auditor: sync failing", titles)
        self.assertIn("Intervals auditor: sync working again", titles)
        self.assertEqual(self.r.runs.recent(1)[0].status, "ok")


if __name__ == "__main__":
    unittest.main()
