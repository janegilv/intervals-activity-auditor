import contextlib
import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intervals_auditor.app import App
from intervals_auditor.cli import main as cli
from intervals_auditor.config import PROJECT_ROOT, load_settings
from intervals_auditor.errors import ConfigError
from intervals_auditor.logging_setup import reset_logging

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


class ConfigTest(unittest.TestCase):
    def test_paths_numbers_and_local_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.toml").write_text(CHECKS_TOML)
            (root / "checks.local.toml").write_text('[duplicate_overlap]\nignore_activities = ["i9"]\n')
            s = load_settings(root, {"DATA_DIR": "state", "REFRESH_DAYS": "14", "NOTIFIERS": "pushover"})
            self.assertEqual(s.db_path, root / "state" / "auditor.sqlite")
            self.assertEqual(s.log_file, root / "state" / "logs" / "auditor.log")
            self.assertEqual(s.refresh_days, 14)
            self.assertEqual(s.notifier_names, ["pushover"])
            self.assertEqual(s.checks["duplicate_overlap"]["ignore_activities"], ["i9"])
            self.assertTrue(s.checks["duplicate_overlap"]["enabled"])
            self.assertFalse(s.has_intervals_credentials)
            with self.assertRaises(ConfigError):
                s.require_intervals()
            with self.assertRaises(ConfigError):
                load_settings(root, {"REFRESH_DAYS": "a month"})

    def test_repository_checks_toml_loads(self):
        s = load_settings(PROJECT_ROOT, {})
        self.assertIn("duplicate_overlap", s.checks)


class CliTest(unittest.TestCase):
    """Drives the CLI end to end on a temporary project with stored data."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "checks.toml").write_text(CHECKS_TOML)
        self.settings = load_settings(self.root, {})
        with App(self.settings) as app:
            with app.db.transaction():
                app.activities.upsert_many([
                    payload("i1", start="2026-09-20T07:00:00"),
                    payload("i2", start="2026-09-20T07:00:30"),
                    payload("i3", start="2026-09-21T07:00:00", seconds=30),
                ], "2026-09-20T10:00:00+00:00")

    def tearDown(self):
        reset_logging()
        logging.getLogger("intervals_auditor").addHandler(logging.NullHandler())
        self.tmp.cleanup()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "load_settings", return_value=self.settings), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_workflow(self):
        code, out, _ = self.cli("--offline", "-q")
        self.assertEqual(code, 0)
        self.assertIn("3 open issues on 3 activities", out)
        self.assertTrue((self.root / "output" / "report.md").exists())
        self.assertTrue(self.settings.log_file.exists())

        code, out, _ = self.cli("ack", "i1", "-n", "both real")
        self.assertEqual(code, 0)
        self.assertIn("Acknowledged 2 issue(s)", out)
        self.assertIn("https://intervals.icu/activities/i1", out)

        code, out, _ = self.cli("issues")
        self.assertIn("1 open issue on 1 activity (2 acknowledged not shown", out)
        code, out, _ = self.cli("issues", "--all")
        self.assertIn("acknowledged #", out)

        code, out, _ = self.cli("show", "i2")
        self.assertIn("ACKNOWLEDGED", out)
        self.assertIn("note: both real", out)

        code, out, _ = self.cli("acks")
        self.assertIn("2 acknowledgment(s)", out)
        code, out, _ = self.cli("unack", "i2", "--only-this")
        self.assertIn("Revoked 1 acknowledgment(s)", out)
        code, out, _ = self.cli("acks", "--all")
        self.assertIn("revoked", out)

        code, out, _ = self.cli("status")
        self.assertIn("Activities      3", out)
        code, out, _ = self.cli("monitor", "--offline", "--dry-run", "-q")
        self.assertEqual(code, 0)
        code, out, _ = self.cli("checks")
        self.assertIn("✓ duplicate_overlap", out)

    def test_errors(self):
        code, _, err = self.cli("show", "nope")
        self.assertEqual(code, 1)
        code, _, err = self.cli("ack", "nope")
        self.assertEqual(code, 1)
        self.assertIn("No activity 'nope'", err)
        code, _, err = self.cli("run", "-q")  # no credentials
        self.assertEqual(code, 2)
        self.assertIn("INTERVALS_API_KEY", err)
        code, _, err = self.cli("notify-test")
        self.assertEqual(code, 1)


class LegacyDatabaseTest(unittest.TestCase):
    def test_old_cache_database_is_copied_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.toml").write_text(CHECKS_TOML)
            settings = load_settings(root, {})
            with App(load_settings(root, {"DB_PATH": str(settings.legacy_db_path)})) as app:
                with app.db.transaction():
                    app.activities.upsert_many([payload("i1")], "t")
            with App(settings) as app:
                self.assertEqual(app.activities.count(), 1)
            self.assertTrue(settings.legacy_db_path.exists())


if __name__ == "__main__":
    unittest.main()
