import unittest

from intervals_auditor.repositories import CheckSettingRepository
from intervals_auditor.services.audit import AuditService
from intervals_auditor.services.check_settings import CheckSettingsError, CheckSettingsService

from tests.helpers import FakeClock, Repos, payload

FILE_CONFIG = {
    "under_time_limit": {"enabled": True, "maximum_duration_seconds": 100},
    "missing_distance": {"enabled": True, "minimum_moving_seconds": 60, "outdoor_types": ["Ride", "Run"]},
    "implausible_pace": {"enabled": True, "margin_kmh": 2.0, "speed_kmh_by_type": {}},
}


class CheckSettingsTest(unittest.TestCase):
    def setUp(self):
        self.r = Repos()
        self.svc = CheckSettingsService(self.r.db, CheckSettingRepository(self.r.db), FILE_CONFIG, clock=FakeClock())

    def test_file_values_until_changed(self):
        s = self.svc.get("under_time_limit")
        self.assertTrue(s.enabled)
        self.assertFalse(s.changed)
        self.assertEqual([(p.key, p.value, p.kind) for p in s.params], [("maximum_duration_seconds", 100, "int")])
        self.assertFalse(self.svc.get("moving_elapsed_gap").enabled)  # not in the file at all

    def test_update_is_layered_on_top_and_equal_value_removes_the_change(self):
        s = self.svc.update("under_time_limit", {"maximum_duration_seconds": 300, "enabled": False})
        self.assertTrue(s.changed and s.enabled_changed)
        eff = self.svc.effective()
        self.assertEqual(eff["under_time_limit"], {"enabled": False, "maximum_duration_seconds": 300})
        self.assertEqual(FILE_CONFIG["under_time_limit"]["maximum_duration_seconds"], 100)  # file untouched
        s = self.svc.update("under_time_limit", {"maximum_duration_seconds": 100, "enabled": True})
        self.assertFalse(s.changed)
        self.assertEqual(self.r.db.conn.execute("SELECT COUNT(*) FROM check_settings").fetchone()[0], 0)

    def test_types_are_checked_against_the_file(self):
        self.svc.update("implausible_pace", {"margin_kmh": 3})  # int accepted for a float setting
        self.assertEqual(self.svc.effective()["implausible_pace"]["margin_kmh"], 3.0)
        self.svc.update("missing_distance", {"outdoor_types": ["Ride"]})
        self.svc.update("implausible_pace", {"speed_kmh_by_type": {"Run": [3, 25]}})
        for name, values in (("under_time_limit", {"maximum_duration_seconds": "300"}),
                             ("under_time_limit", {"maximum_duration_seconds": True}),
                             ("under_time_limit", {"enabled": "yes"}),
                             ("missing_distance", {"outdoor_types": "Ride"}),
                             ("under_time_limit", {"no_such_setting": 1}),
                             ("no_such_check", {"enabled": True})):
            with self.assertRaises(CheckSettingsError, msg=(name, values)):
                self.svc.update(name, values)

    def test_reset(self):
        self.svc.update("missing_distance", {"minimum_moving_seconds": 5, "enabled": False})
        self.assertFalse(self.svc.reset("missing_distance").changed)

    def test_checks_run_with_the_changed_settings(self):
        self.r.add(payload("i1", seconds=200, distance=500.0))
        audit = lambda: AuditService(self.r.db, self.r.activities, self.r.issues, self.r.acks,
                                     self.svc.effective(), clock=FakeClock()).run()
        self.assertEqual(audit().new, [])
        self.svc.update("under_time_limit", {"maximum_duration_seconds": 300})
        self.assertEqual([r.key for r in audit().new], ["i1|under_time_limit"])


if __name__ == "__main__":
    unittest.main()
