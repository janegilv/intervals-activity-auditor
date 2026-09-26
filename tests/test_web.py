import tempfile
import threading
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient

    from intervals_auditor.web.api import create_app
    from intervals_auditor.web.jobs import JobBusy, JobManager, MonitorScheduler
    HAVE_WEB = True
except ImportError:  # the web extras (requirements-web.txt + httpx) aren't installed
    HAVE_WEB = False

from intervals_auditor.app import App
from intervals_auditor.config import load_settings

from tests.helpers import payload

CHECKS_TOML = """
[duplicate_overlap]
enabled = true
minimum_overlap_percent = 10.0
require_same_type = false
[under_time_limit]
enabled = true
maximum_duration_seconds = 100
"""


@unittest.skipUnless(HAVE_WEB, "web extras not installed")
class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "checks.toml").write_text(CHECKS_TOML)
        self.settings = load_settings(root, {})
        with App(self.settings) as app:
            with app.db.transaction():
                app.activities.upsert_many([
                    payload("i101", start="2026-09-20T07:00:00"),
                    payload("i102", start="2026-09-20T07:00:30"),
                    payload("i103", start="2026-09-21T07:00:00", seconds=30),
                ], "2026-09-20T10:00:00+00:00")
            app.audit.run()  # issues 1+2: the i101/i102 pair, 3: i103 too short
        self.web = create_app(self.settings, scheduler=False)
        self.client = TestClient(self.web)

    def tearDown(self):
        self.web.state.jobs.wait(5)
        self.client.close()
        self.tmp.cleanup()

    def issues(self):
        r = self.client.get("/api/issues")
        self.assertEqual(r.status_code, 200)
        return {i["number"]: i for a in r.json()["activities"] for i in a["issues"]}

    def test_pages_and_assets(self):
        self.assertIn("Intervals auditor", self.client.get("/").text)
        self.assertEqual(self.client.get("/ui/issues.js").status_code, 200)
        self.assertEqual(self.client.get("/ui/base.css").status_code, 200)
        self.assertEqual(self.client.get("/ui/__init__.py").status_code, 404)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/healthz").json(), {"ok": True})
        self.assertIn("AuditorUI", self.client.get("/report.html").text)
        self.assertEqual(self.client.get("/api/docs").status_code, 200)

    def test_ack_and_unack(self):
        self.assertEqual({n: i["state"] for n, i in self.issues().items()}, {1: "open", 2: "open", 3: "open"})
        r = self.client.post("/api/acks", json={"issues": [3], "note": "false start"})
        self.assertEqual(r.json()["count"], 1)
        self.assertEqual(self.issues()[3]["acknowledgment"]["note"], "false start")
        self.assertTrue((self.settings.output_dir / "report.html").exists())  # reports refreshed
        r = self.client.post("/api/acks", json={"issues": [1]})  # the mirror (2) comes along
        self.assertEqual(sorted(a["issue_number"] for a in r.json()["created"]), [1, 2])
        r = self.client.post("/api/unacks", json={"issues": [1], "only_this": True})
        self.assertEqual([a["issue_number"] for a in r.json()["revoked"]], [1])
        self.assertEqual({n: i["state"] for n, i in self.issues().items()},
                         {1: "open", 2: "acknowledged", 3: "acknowledged"})
        acks = self.client.get("/api/acks", params={"include_revoked": True}).json()["acks"]
        self.assertEqual(sorted((a["issue_number"], a["active"]) for a in acks),
                         [(1, False), (2, True), (3, True)])
        r = self.client.post("/api/acks", json={"activities": ["102"], "note": "by activity"})  # "i" optional
        self.assertEqual(r.status_code, 200)

    def test_ack_errors(self):
        r = self.client.post("/api/acks", json={"issues": [99], "activities": ["nope"]})
        self.assertEqual(r.status_code, 404)
        self.assertIn("99, nope", r.json()["detail"])
        self.assertEqual(self.client.post("/api/acks", json={}).status_code, 422)
        self.assertEqual(self.client.post("/api/acks", json={"issues": ["x"]}).status_code, 422)

    def test_activity_detail(self):
        by_id = self.client.get("/api/activities/i103").json()
        by_issue = self.client.get("/api/activities/3").json()
        self.assertEqual(by_id["id"], "i103")
        self.assertEqual(by_issue["id"], "i103")
        self.assertEqual([i["number"] for i in by_id["issues"]], [3])
        self.assertEqual(self.client.get("/api/activities/nope").status_code, 404)

    def test_check_settings_rerun_the_checks(self):
        checks = {c["name"]: c for c in self.client.get("/api/checks").json()["checks"]}
        self.assertEqual(checks["under_time_limit"]["params"][0]["value"], 100)
        r = self.client.put("/api/checks/under_time_limit", json={"values": {"maximum_duration_seconds": 5000}})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["job"])
        self.web.state.jobs.wait(10)
        self.assertEqual(self.web.state.jobs.status()["last"]["status"], "ok")
        under = [i for i in self.issues().values() if i["check"] == "under_time_limit"]
        self.assertEqual(len(under), 3)  # every activity is now "too short"
        self.assertEqual(self.client.put("/api/checks/under_time_limit",
                                         json={"values": {"maximum_duration_seconds": "x"}}).status_code, 422)
        self.assertEqual(self.client.put("/api/checks/nope", json={"values": {}}).status_code, 404)
        r = self.client.delete("/api/checks/under_time_limit")
        self.assertFalse(r.json()["check"]["changed"])

    def test_sync_job_without_credentials_fails_cleanly(self):
        r = self.client.post("/api/jobs", json={"kind": "sync"})
        self.assertEqual(r.status_code, 202)
        self.web.state.jobs.wait(10)
        last = self.client.get("/api/jobs").json()["last"]
        self.assertEqual(last["status"], "failed")
        self.assertIn("INTERVALS_API_KEY", last["message"])
        self.assertEqual(self.client.post("/api/jobs", json={"kind": "nope"}).status_code, 422)

    def test_status_runs_logs(self):
        s = self.client.get("/api/status").json()
        self.assertEqual((s["activity_count"], s["open_issues"]), (3, 3))
        self.assertFalse(s["monitor"]["enabled"])
        self.assertEqual(len(self.client.get("/api/runs").json()["runs"]), 0)
        self.assertEqual(self.client.get("/api/logs").json()["lines"], [])


@unittest.skipUnless(HAVE_WEB, "web extras not installed")
class JobsTest(unittest.TestCase):
    def test_one_job_at_a_time(self):
        started, release = threading.Event(), threading.Event()

        class SlowApp:
            def __init__(self, settings): pass
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def lock(self): return self

            class runner:
                @staticmethod
                def run(**kwargs):
                    started.set(); release.wait(5)
                    raise RuntimeError("stopped")

        jobs = JobManager(settings=None, app_factory=SlowApp)
        jobs.start("sync")
        started.wait(5)
        with self.assertRaises(JobBusy):
            jobs.start("checks")
        release.set()
        jobs.wait(5)
        self.assertEqual(jobs.status()["last"]["status"], "failed")
        self.assertIsNone(jobs.status()["current"])

    def test_scheduler_runs_the_monitor(self):
        calls = []

        class FakeJobs:
            def run(self, kind):
                calls.append(kind)

        scheduler = MonitorScheduler(FakeJobs(), interval_minutes=0.001, first_delay_seconds=0)
        scheduler.start()
        deadline = time.time() + 5
        while len(calls) < 2 and time.time() < deadline:
            time.sleep(0.02)
        scheduler.stop()
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(set(calls), {"monitor"})
        self.assertFalse(MonitorScheduler(FakeJobs(), interval_minutes=0).enabled)


if __name__ == "__main__":
    unittest.main()
