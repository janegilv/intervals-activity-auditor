import unittest

from intervals_auditor.checks import CHECKS
from intervals_auditor.checks.duplicate_overlap import DuplicateOverlapCheck
from intervals_auditor.checks.garmin_link_only import GarminLinkOnlyCheck
from intervals_auditor.checks.under_time_limit import UnderTimeLimitCheck

from tests.helpers import Repos, payload


def activities(*payloads):
    r = Repos()
    r.add(*payloads)
    return r.activities.all()


DUP_CONFIG = {"minimum_overlap_percent": 10.0, "require_same_type": False}


class DuplicateOverlapTest(unittest.TestCase):
    def test_pair_gives_one_issue_per_activity(self):
        acts = activities(payload("i1", start="2026-09-20T07:00:00"),
                          payload("i2", start="2026-09-20T07:00:30", name="Copy"))
        issues = DuplicateOverlapCheck(DUP_CONFIG).run(acts)
        self.assertEqual({(i.activity_id, i.related_activity_ids) for i in issues},
                         {("i1", ("i2",)), ("i2", ("i1",))})
        by_activity = {i.activity_id: i for i in issues}
        self.assertIn('i2 "Copy"', by_activity["i1"].summary)
        self.assertEqual(by_activity["i1"].severity, by_activity["i2"].severity)
        self.assertNotEqual(by_activity["i1"].key, by_activity["i2"].key)

    def test_ignored_activity_is_skipped_on_either_side(self):
        acts = activities(payload("i1", start="2026-09-20T07:00:00"),
                          payload("i2", start="2026-09-20T07:00:30"))
        for ignored in ("i1", "i2"):
            config = dict(DUP_CONFIG, ignore_activities=[ignored])
            self.assertEqual(DuplicateOverlapCheck(config).run(acts), [], ignored)

    def test_zero_duration_stub_inside_activity(self):
        acts = activities(payload("i1", start="2026-09-20T07:00:00"),
                          payload("999", start="2026-09-20T07:30:00", seconds=0, source="STRAVA"))
        issues = {i.activity_id: i for i in DuplicateOverlapCheck(DUP_CONFIG).run(acts)}
        self.assertIn("Has no recorded duration", issues["999"].summary)
        self.assertIn("Contains the timestamp", issues["i1"].summary)


class SingleActivityChecksTest(unittest.TestCase):
    def test_under_time_limit(self):
        issues = UnderTimeLimitCheck({"maximum_duration_seconds": 100}).run(
            activities(payload("i1", seconds=30), payload("i2")))
        self.assertEqual([(i.activity_id, i.check, i.related_activity_ids) for i in issues],
                         [("i1", "under_time_limit", ())])

    def test_garmin_link_only_handles_missing_elapsed_time(self):
        acts = activities(payload("i1", strava_id=None, elapsed_time=None, moving_time=None))
        issues = GarminLinkOnlyCheck().run(acts)
        self.assertEqual(len(issues), 1)
        self.assertIn("SHORT DURATION", issues[0].details[0]["details"])

    def test_registry(self):
        self.assertEqual(set(CHECKS), {
            "duplicate_overlap", "under_time_limit", "over_time_limit", "garmin_link_only",
            "implausible_pace", "missing_distance", "moving_elapsed_gap",
        })


if __name__ == "__main__":
    unittest.main()
