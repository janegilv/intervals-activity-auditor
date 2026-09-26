import json
import re
import tempfile
import unittest
from pathlib import Path

from intervals_auditor.reporting import write_reports
from intervals_auditor.reporting.html_report import PLACEHOLDER, render
from intervals_auditor.services.acks import AckService
from intervals_auditor.services.audit import AuditService
from intervals_auditor.services.queries import QueryService

from tests.helpers import FakeClock, Repos, payload

CONFIG = {"under_time_limit": {"enabled": True, "maximum_duration_seconds": 100}}


class HtmlReportTest(unittest.TestCase):
    def setUp(self):
        self.r = Repos()
        clock = FakeClock()
        self.r.add(payload("i1", seconds=30, name='Evil </script><script>alert(1)</script> & "quotes"'),
                   payload("i2", seconds=20, start="2026-09-21T07:00:00"))
        AuditService(self.r.db, self.r.activities, self.r.issues, self.r.acks, CONFIG, clock=clock).run()
        AckService(self.r.db, self.r.activities, self.r.issues, self.r.acks, clock=clock).acknowledge("i2", note="ok")
        self.q = QueryService(self.r.activities, self.r.issues, self.r.acks, self.r.state, self.r.runs)

    def embedded(self, html: str) -> dict:
        m = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', html, re.S)
        self.assertIsNotNone(m)
        return json.loads(m.group(1))

    def test_data_is_embedded_and_cannot_break_out_of_the_script_tag(self):
        html = render(self.q.report_views(), "2026-09-26T10:00:00+00:00", {"last_sync": "x"})
        self.assertNotIn(PLACEHOLDER, html)
        self.assertEqual(html.count("</script>"), 2)  # the data block and the app script
        data = self.embedded(html)
        self.assertEqual(data["last_sync"], "x")
        self.assertEqual(data["command"], "python main.py")
        self.assertIn("under_time_limit", data["checks"])
        names = {a["id"]: a["name"] for a in data["activities"]}
        self.assertIn("</script>", names["i1"])  # restored intact by JSON.parse
        states = {a["id"]: a["issues"][0]["state"] for a in data["activities"]}
        self.assertEqual(states, {"i1": "open", "i2": "acknowledged"})
        self.assertEqual(data["activities"][0]["issues"][0]["number"] is not None, True)

    def test_write_reports_puts_html_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(self.q.report_views(), Path(tmp), "t", self.q.report_meta())
            self.assertEqual([p.name for p in paths], ["report.html", "report.md", "report.json", "report.csv"])
            self.assertTrue(all(p.exists() for p in paths))


if __name__ == "__main__":
    unittest.main()
