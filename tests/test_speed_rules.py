"""The implausible_pace speed rules (top speed, sustained speed by grade) and
the activity streams they use."""
import tempfile
import unittest
from pathlib import Path

from intervals_auditor import streams as st
from intervals_auditor.app import App
from intervals_auditor.checks.implausible_pace import ImplausiblePaceCheck
from intervals_auditor.config import load_settings
from intervals_auditor.repositories import StreamRepository
from intervals_auditor.services.check_settings import CheckSettingRepository, CheckSettingsError, CheckSettingsService
from intervals_auditor.services.streams import StreamService
from intervals_auditor.sources.intervals import IntervalsRateLimitError
from intervals_auditor.timeutil import utcnow

from tests.helpers import FakeClock, Repos, payload

GPS = {"stream_types": ["time", "distance", "altitude", "latlng"], "analyzed": "2026-09-20T10:00:00Z"}


def api_streams(segments, start_altitude=100.0):
    """Intervals.icu-style streams at 1 s from (seconds, km/h, grade %) segments."""
    time, distance, altitude = [0], [0.0], [start_altitude]
    for seconds, kmh, grade in segments:
        for _ in range(seconds):
            step = kmh / 3.6
            time.append(time[-1] + 1)
            distance.append(distance[-1] + step)
            altitude.append(altitude[-1] + step * grade / 100)
    return [{"type": "time", "data": time}, {"type": "distance", "data": distance},
            {"type": "altitude", "data": altitude}]


def activity(activity_id="i1", type_="Ride", **extra):
    from intervals_auditor.fingerprint import activity_fingerprint
    from intervals_auditor.models import Activity
    p = payload(activity_id, type_=type_, **{**GPS, **extra})
    return Activity(id=activity_id, name=p["name"], activity_type=type_, start_date_local=p["start_date_local"],
                    start_date=p["start_date"], moving_time=p["moving_time"], elapsed_time=p["elapsed_time"],
                    distance=p["distance"], raw=p, fingerprint=activity_fingerprint(p))


def run_check(activities, streams_by_id=None, config=None):
    streams_by_id = streams_by_id or {}
    check = ImplausiblePaceCheck(config or {}, streams=lambda a: streams_by_id.get(a.id))
    return {i.activity_id: i for i in check.run(activities)}


class StreamsTest(unittest.TestCase):
    def test_parse_decimate_and_round_trip(self):
        parsed = st.parse_api(api_streams([(600, 20, 0)]))
        self.assertEqual(parsed.time[:3], [0, 5, 10])  # a point every 5 s
        self.assertEqual(parsed.time[-1], 600)
        again = st.decode(st.encode(parsed))
        self.assertEqual(again.time, parsed.time)
        self.assertAlmostEqual(again.distance[-1], parsed.distance[-1], places=0)
        self.assertLess(len(st.encode(parsed)), 2000)

    def test_mapping_format_missing_altitude_and_gaps(self):
        parsed = st.parse_api({"time": [0, 1, None, 10], "distance": [0, 5, 7, 50]})
        self.assertEqual((parsed.time, parsed.altitude), ([0.0, 10.0], None))
        self.assertIsNone(st.parse_api([{"type": "time", "data": [0, 1]}]))  # no distance
        with_nones = st.parse_api({"time": [0, 5, 10], "distance": [0, 10, 20], "altitude": [None, 5, None]})
        self.assertEqual(with_nones.altitude, [5, 5])  # waits for the first value, then carries it forward
        self.assertIsNone(st.parse_api([]))  # no streams at all
        for odd in ({"streams": None}, [1, 2], "x"):  # not what we expect: an error, retried later
            with self.assertRaises(st.UnknownStreamsFormat):
                st.parse_api(odd)

    def test_corrected_elevation_wins(self):
        device = api_streams([(600, 50, 5)])  # the device thinks this was a climb...
        corrected = [s for s in api_streams([(600, 50, 0)]) if s["type"] == "altitude"]
        corrected[0]["type"] = "fixed_altitude"  # ...Intervals.icu's corrected elevation says flat
        parsed = st.parse_api(device + corrected)
        self.assertEqual(parsed.altitude[0], parsed.altitude[-1])
        self.assertEqual(run_check([activity("i1")], {"i1": parsed}), {})
        self.assertIn("at +5% grade", run_check([activity("i1")], {"i1": st.parse_api(device)})["i1"].summary)
        # An empty corrected stream falls back to the device's altitude.
        corrected[0]["data"] = [None] * len(corrected[0]["data"])
        self.assertNotEqual(st.parse_api(device + corrected).altitude[-1], st.parse_api(device).altitude[0])

    def test_windows_speed_grade_and_glitches(self):
        s = st.parse_api(api_streams([(300, 36, 5)]))
        ws = list(st.windows(s, 60))
        self.assertAlmostEqual(ws[10].speed_kmh, 36, delta=0.5)
        self.assertAlmostEqual(ws[10].grade_percent, 5, delta=0.3)
        # One point 2 km off (and back) doesn't make the minute fast.
        glitch = api_streams([(300, 10, 0)])
        glitch[1]["data"][150] += 2000
        worst = max(w.speed_kmh for w in st.windows(st.parse_api(glitch), 60))
        self.assertLess(worst, 25)

    def test_interpolate(self):
        table = [[0, 60], [6, 30], [-8, 100]]
        self.assertEqual(st.interpolate(table, 3), 45)
        self.assertEqual(st.interpolate(table, 20), 30)
        self.assertEqual(st.interpolate(table, -20), 100)


class SpeedRulesTest(unittest.TestCase):
    def test_top_speed(self):
        hike = activity("i1", "Hike", max_speed=17.68, distance=3800.0, moving_time=2000)
        trainer = activity("i2", "Ride", max_speed=30.0, trainer=True)
        found = run_check([hike, trainer])
        self.assertEqual(list(found), ["i1"])
        self.assertIn("Top speed 63.6 km/h", found["i1"].summary)
        self.assertEqual(found["i1"].severity, "high")  # more than twice the 20 km/h limit
        self.assertEqual(found["i1"].details[0]["rule"], "max_speed_above_limit")

    def test_average_speed_keeps_its_message(self):
        run = activity("i1", "Run", distance=40000.0, moving_time=3600)
        self.assertEqual(run_check([run])["i1"].summary,
                         "Average speed (40.0 km/h) is implausibly above the expected range for Run "
                         "(3-30 km/h, ±2 km/h margin)")

    def test_car_uphill_is_found_fast_descent_is_not(self):
        ride = activity("i1")
        streams = st.parse_api(api_streams([(1200, 25, 0), (180, 50, 6), (600, 25, 0)]))
        issue = run_check([ride], {"i1": streams})["i1"]
        detail = issue.details[0]
        self.assertEqual(detail["rule"], "sustained_speed_above_limit")
        self.assertEqual(detail["too_fast_from"][:4], "0:20")  # 20 minutes in
        self.assertAlmostEqual(detail["grade_percent"], 6, delta=0.5)
        self.assertIn("50 km/h for 60 s at +6% grade", issue.summary)
        self.assertIn("too fast for about 3 min in total from 0:20", issue.summary)
        self.assertEqual(issue.severity, "high")
        descent = st.parse_api(api_streams([(600, 25, 0), (300, 80, -6), (300, 55, 0), (300, 30, 5)]))
        self.assertEqual(run_check([ride], {"i1": descent}), {})

    def test_lift_on_a_hike(self):
        hike = activity("i1", "Hike", distance=6000.0, moving_time=4000)
        streams = st.parse_api(api_streams([(1800, 5, 5), (300, 21.6, 26), (1800, 5, -5)]))
        self.assertIn("at +26% grade", run_check([hike], {"i1": streams})["i1"].summary)

    def test_without_streams_or_altitude(self):
        ride = activity("i1")
        self.assertEqual(run_check([ride]), {})  # streams not fetched yet
        flat_fast = st.parse_api([s for s in api_streams([(300, 90, 0)]) if s["type"] != "altitude"])
        self.assertEqual(run_check([ride], {"i1": flat_fast}), {})  # unknown grade: loosest limit (100)
        faster = st.parse_api([s for s in api_streams([(300, 120, 0)]) if s["type"] != "altitude"])
        self.assertIn("unknown grade", run_check([ride], {"i1": faster})["i1"].summary)

    def test_all_findings_in_one_issue(self):
        ride = activity("i1", max_speed=33.0)
        streams = st.parse_api(api_streams([(600, 25, 0), (180, 50, 6)]))
        issue = run_check([ride], {"i1": streams})["i1"]
        self.assertEqual([d["rule"] for d in issue.details], ["max_speed_above_limit", "sustained_speed_above_limit"])
        self.assertEqual(issue.summary.count(";"), 1)

    def test_tables_are_used_as_given(self):
        run = activity("i1", "Run", distance=40000.0, moving_time=3600, max_speed=20.0)
        self.assertEqual(run_check([run], config={"speed_kmh_by_type": {}, "max_speed_kmh_by_type": {}}), {})
        self.assertEqual(ImplausiblePaceCheck.stream_activity_types({"sustained_speed_kmh_by_grade": {"Hike": [[0, 9]]}}),
                         {"Hike"})

    def test_validation(self):
        ok = {"sustained_speed_kmh_by_grade": {"Ride": [[0, 60], [6, 30]]}, "max_speed_kmh_by_type": {"Hike": 20}}
        ImplausiblePaceCheck.validate_config(ok)
        for bad in ({"sustained_speed_kmh_by_grade": {"Ride": [[0, "fast"]]}},
                    {"sustained_speed_kmh_by_grade": {"Ride": [0, 60]}},
                    {"sustained_speed_kmh_by_grade": {"Ride": []}},
                    {"max_speed_kmh_by_type": {"Hike": "20"}},
                    {"speed_kmh_by_type": {"Run": [3]}},
                    {"sustained_seconds": 1}):
            with self.assertRaises(ValueError, msg=bad):
                ImplausiblePaceCheck.validate_config(bad)

    def test_web_changes_are_validated(self):
        r = Repos()
        file_config = {"implausible_pace": {"enabled": True, "max_speed_kmh_by_type": {"Hike": 20},
                                            "sustained_speed_kmh_by_grade": {"Ride": [[0, 60]]}}}
        svc = CheckSettingsService(r.db, CheckSettingRepository(r.db), file_config, clock=utcnow)
        with self.assertRaises(CheckSettingsError):
            svc.update("implausible_pace", {"sustained_speed_kmh_by_grade": {"Ride": "fast"}})
        svc.update("implausible_pace", {"max_speed_kmh_by_type": {"Hike": 25, "Walk": 20}})
        self.assertEqual(svc.effective()["implausible_pace"]["max_speed_kmh_by_type"]["Hike"], 25)


class FakeStreamsClient:
    def __init__(self, streams=None):
        self.streams = streams or {}
        self.calls = []
        self.types = {}
        self.fail = set()
        self.rate_limit_after = None

    def get_streams(self, activity_id, types):
        if self.rate_limit_after is not None and len(self.calls) >= self.rate_limit_after:
            raise IntervalsRateLimitError("slow down")
        self.calls.append(activity_id)
        self.types[activity_id] = types
        if activity_id in self.fail:
            raise ConnectionError("network down")
        return self.streams.get(activity_id)


class StreamServiceTest(unittest.TestCase):
    def setUp(self):
        self.r = Repos()
        self.clock = FakeClock()
        self.repo = StreamRepository(self.r.db)
        self.client = FakeStreamsClient({f"i{n}": api_streams([(120, 20, 0)]) for n in range(1, 6)})
        self.svc = StreamService(self.r.db, self.r.activities, self.repo, self.client, clock=self.clock)
        self.r.add(
            payload("i1", start="2026-09-20T07:00:00", **GPS),
            payload("i2", start="2026-09-21T07:00:00", **GPS),
            payload("i3", start="2026-09-22T07:00:00", trainer=True, **GPS),        # indoor: skipped
            payload("i4", start="2026-09-23T07:00:00", type_="Hike", **GPS),       # not asked for
            payload("i5", start="2026-09-24T07:00:00", stream_types=["time"]),     # no distance stream
            payload("i6", start="2026-09-25T07:00:00", **GPS),                     # has none
        )

    def test_fetches_whats_needed_newest_first(self):
        self.assertEqual([a.id for a in self.svc.pending({"Ride"})], ["i6", "i2", "i1"])
        result = self.svc.fetch({"Ride"}, limit=2)
        self.assertEqual(self.client.calls, ["i6", "i2"])
        self.assertEqual((result.fetched, result.unavailable, result.remaining), (1, 1, 1))
        self.svc.fetch({"Ride"})
        self.assertEqual(self.client.calls, ["i6", "i2", "i1"])  # i6 (none) isn't asked again
        self.assertEqual(self.svc.pending({"Ride"}), [])
        self.assertEqual(len(self.repo.get("i1")), 25)

    def test_changed_activity_is_fetched_again_and_deleted_ones_go(self):
        self.svc.fetch({"Ride"})
        self.r.add(payload("i1", start="2026-09-20T07:00:00", **{**GPS, "analyzed": "2026-09-26T08:00:00Z"}))
        self.assertEqual([a.id for a in self.svc.pending({"Ride"})], ["i1"])
        activity = self.r.activities.get("i1")
        self.assertIsNone(self.repo.get("i1", st.version_of(activity)))  # stale until fetched again
        with self.r.db.transaction():
            self.r.activities.delete_many(["i2"])
        self.assertNotIn("i2", self.repo.records())

    def test_turning_on_elevation_correction_fetches_it(self):
        self.svc.fetch({"Ride"})
        self.assertEqual(self.client.types["i1"], ("time", "distance", "altitude"))
        self.assertEqual(st.version_of(self.r.activities.get("i1")),
                         f"{self.r.activities.get('i1').fingerprint}|{GPS['analyzed']}")  # unchanged format
        self.r.add(payload("i1", start="2026-09-20T07:00:00", use_elevation_correction=True,
                           **{**GPS, "stream_types": GPS["stream_types"] + ["fixed_altitude"]}))
        self.assertEqual([a.id for a in self.svc.pending({"Ride"})], ["i1"])
        self.svc.fetch({"Ride"})
        self.assertEqual(self.client.types["i1"], ("time", "distance", "altitude", "fixed_altitude"))

    def test_unexpected_responses_are_retried(self):
        self.client.streams["i6"] = {"message": "something else"}
        result = self.svc.fetch({"Ride"})
        self.assertIn("i6", result.errors)
        self.assertEqual([a.id for a in self.svc.pending({"Ride"})], ["i6"])

    def test_errors_are_retried_and_rate_limits_stop(self):
        self.client.fail = {"i6"}
        result = self.svc.fetch({"Ride"})
        self.assertEqual((result.fetched, len(result.errors)), (2, 1))
        self.assertEqual([a.id for a in self.svc.pending({"Ride"})], ["i6"])
        self.client.fail = set()
        self.client.rate_limit_after = len(self.client.calls)
        result = self.svc.fetch({"Ride"})
        self.assertTrue(result.rate_limited)
        self.assertEqual(result.remaining, 1)


class FakeIntervals(FakeStreamsClient):
    def __init__(self, payloads, streams):
        super().__init__(streams)
        self.payloads = payloads

    def list_activities(self, oldest, newest):
        return list(self.payloads)


class RunWithStreamsTest(unittest.TestCase):
    def test_a_run_fetches_streams_and_finds_the_car(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.toml").write_text("[implausible_pace]\nenabled = true\n")
            settings = load_settings(root, {"INTERVALS_API_KEY": "k", "INTERVALS_ATHLETE_ID": "1",
                                            "STREAMS_PER_RUN": "5"})
            client = FakeIntervals(
                [payload("i1", start="2026-09-20T07:00:00", **GPS), payload("i2", start="2026-09-21T07:00:00", **GPS)],
                {"i1": api_streams([(600, 25, 0)]), "i2": api_streams([(600, 25, 0), (300, 60, 8)])})
            with App(settings, intervals_client=client, clock=FakeClock()) as app:
                result = app.runner.run(reports=False)
                self.assertEqual(result.streams.fetched, 2)
                self.assertEqual([r.activity_id for r in result.audit.new], ["i2"])
                self.assertEqual(app.stream_status(), {"stored": 2, "unavailable": 0, "waiting": 0,
                                                       "types": sorted(ImplausiblePaceCheck.stream_activity_types({}))})
                result = app.runner.run(reports=False)  # nothing new to fetch
                self.assertEqual(result.streams.fetched, 0)
                self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
