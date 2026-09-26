"""Speeds that don't fit the activity type -- a GPS glitch, the wrong
activity type, or part of the activity done in a car, bus, lift or train.

Three rules, each with a table per activity type in checks.toml (a type
that isn't in a table isn't checked by that rule):

- average speed outside a plausible range (speed_kmh_by_type, widened by
  margin_kmh);
- a top speed above what's plausible (max_speed_kmh_by_type): the fastest
  10 seconds when the activity's streams are there, so a one-sample GPS
  spike doesn't count (the gps_glitches check reports those), otherwise the
  top speed the device recorded;
- a sustained speed over sustained_seconds that's too fast for the grade
  at that point (sustained_speed_kmh_by_grade). Going downhill you can be
  as fast as a car, uphill you can't -- so this is what finds a car or lift
  ride inside a ride or hike. It needs the activity's streams (time series),
  which are fetched from Intervals.icu for these types. GPS jumps and
  sudden altitude jumps are left out, and in the first
  grade_ignored_first_seconds only the table's loosest limit applies, as
  devices often settle their altitude after the start.

Indoor and trainer sessions are skipped by the top-speed and sustained
rules (their speed is virtual). All findings on one activity make one issue.
"""
from intervals_auditor import derivation
from intervals_auditor.streams import Interpolator, clock, top_speed, windows

from .base import ActivityCheck

# Used when checks.toml doesn't set the table at all; checks.toml lists them
# in full so they can be seen and changed there (or in the web interface).
DEFAULT_SPEED_BOUNDS_KMH = {
    "Run": (3, 30),
    "TrailRun": (2, 30),
    "Walk": (1, 8),
    "Hike": (1, 8),
    "Ride": (3, 70),
    "GravelRide": (3, 65),
    "MountainBikeRide": (2, 55),
    "VirtualRide": (3, 70),
    "NordicSki": (2, 45),
    "AlpineSki": (2, 90),
    "OpenWaterSwim": (1, 8),
    "IceSkate": (2, 45),
    "VirtualRow": (2, 25),
}

DEFAULT_MAX_SPEED_KMH = {
    "Walk": 20, "Hike": 20, "Run": 30, "TrailRun": 35,
    "Ride": 100, "GravelRide": 100, "MountainBikeRide": 80, "EBikeRide": 100,
    "NordicSki": 60, "AlpineSki": 130, "IceSkate": 50,
}

_BIKE = [[-8, 100], [-4, 80], [0, 60], [3, 42], [6, 32], [10, 24], [15, 18]]
DEFAULT_SUSTAINED_SPEED_KMH_BY_GRADE = {
    "Ride": _BIKE, "GravelRide": _BIKE, "MountainBikeRide": _BIKE,
    "EBikeRide": [[-8, 100], [-4, 80], [0, 60], [3, 45], [6, 38], [10, 32], [15, 28]],
    "Run": [[-10, 30], [0, 26], [5, 20], [10, 16], [20, 11], [30, 8]],
    "TrailRun": [[-10, 30], [0, 26], [5, 20], [10, 16], [20, 11], [30, 8]],
    "Walk": [[-15, 22], [0, 18], [10, 12], [20, 9], [30, 7]],
    "Hike": [[-15, 22], [0, 18], [10, 12], [20, 9], [30, 7]],
}
DEFAULT_SUSTAINED_SECONDS = 60
DEFAULT_GRADE_IGNORED_FIRST_SECONDS = 300
# The top speed rule looks at the fastest this many seconds (with streams).
TOP_SPEED_SECONDS = 10


def _sustained_table(config: dict) -> dict:
    return config.get("sustained_speed_kmh_by_grade", DEFAULT_SUSTAINED_SPEED_KMH_BY_GRADE)


class ImplausiblePaceCheck(ActivityCheck):
    name = "implausible_pace"
    description = (
        "Finds activities whose speed doesn't fit their activity type: an average speed outside a "
        "plausible range, a top speed that's too high, or a sustained speed that's too fast for the "
        "grade (e.g. part of it done in a car or lift) -- or a GPS glitch or mislabeled activity type."
    )

    @classmethod
    def stream_activity_types(cls, config: dict) -> set[str]:
        return set(_sustained_table(config))

    @classmethod
    def validate_config(cls, config: dict) -> None:
        def number(value, where):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{where} must be a number, got {value!r}")

        for type_, bounds in config.get("speed_kmh_by_type", {}).items():
            if not (isinstance(bounds, list) and len(bounds) == 2):
                raise ValueError(f"speed_kmh_by_type.{type_} must be [lowest, highest], got {bounds!r}")
            for value in bounds:
                number(value, f"speed_kmh_by_type.{type_}")
        for type_, limit in config.get("max_speed_kmh_by_type", {}).items():
            number(limit, f"max_speed_kmh_by_type.{type_}")
        for type_, table in config.get("sustained_speed_kmh_by_grade", {}).items():
            where = f"sustained_speed_kmh_by_grade.{type_}"
            if not (isinstance(table, list) and table and all(isinstance(p, list) and len(p) == 2 for p in table)):
                raise ValueError(f"{where} must be a list of [grade %, km/h] pairs, got {table!r}")
            for point in table:
                number(point[0], where)
                number(point[1], where)
                if point[1] <= 0:
                    raise ValueError(f"{where}: speeds must be above 0")
        number(config.get("sustained_seconds", DEFAULT_SUSTAINED_SECONDS), "sustained_seconds")
        if config.get("sustained_seconds", DEFAULT_SUSTAINED_SECONDS) < 10:
            raise ValueError("sustained_seconds must be at least 10")
        ignored = config.get("grade_ignored_first_seconds", DEFAULT_GRADE_IGNORED_FIRST_SECONDS)
        number(ignored, "grade_ignored_first_seconds")
        if ignored < 0:
            raise ValueError("grade_ignored_first_seconds can't be negative")

    def run(self, activities):
        issues = []
        for a in activities:
            findings = [f for f in (self._average(a), self._max_speed(a), self._sustained(a)) if f]
            if not findings:
                continue
            severity = "high" if any(f[2] == "high" for f in findings) else "medium"
            issues.append(self.issue(a, "; ".join(f[0] for f in findings), [f[1] for f in findings], severity))
        return issues

    # Each rule returns (summary, detail, severity) or None.

    def _average(self, a):
        bounds = self.config.get("speed_kmh_by_type", DEFAULT_SPEED_BOUNDS_KMH)
        type_bounds = bounds.get(a.activity_type)
        if not type_bounds:
            return None
        speed = derivation.average_speed_kmh(a)
        if speed is None:
            return None
        # A speed just past the line is ordinary variation, not bad data:
        # widen each bound by this margin before flagging anything.
        margin = float(self.config.get("margin_kmh", 2.0))
        low, high = type_bounds
        if low - margin <= speed <= high + margin:
            return None
        if speed > high:
            direction, ratio = "above", speed / high
        else:
            direction, ratio = "below", (low / speed if speed > 0 else float("inf"))
        # Being more than 2x outside the range is a much stronger signal.
        severity = "high" if ratio >= 2 else "medium"
        return (
            f"Average speed ({speed:.1f} km/h) is implausibly {direction} the expected range for "
            f"{a.activity_type} ({low}-{high} km/h, ±{margin:g} km/h margin)",
            {"rule": "speed_out_of_range", "average_speed_kmh": round(speed, 2),
             "expected_range_kmh": [low, high], "margin_kmh": margin, "activity_type": a.activity_type},
            severity,
        )

    def _max_speed(self, a):
        limit = self.config.get("max_speed_kmh_by_type", DEFAULT_MAX_SPEED_KMH).get(a.activity_type)
        if not limit or a.is_trainer or a.is_indoor:
            return None
        recorded = float(a.raw.get("max_speed") or 0) * 3.6  # m/s, as recorded
        streams = self.streams(a)
        if streams is not None:  # the fastest 10 s: a one-sample GPS spike doesn't count
            speed, over = top_speed(streams, TOP_SPEED_SECONDS), f" over {TOP_SPEED_SECONDS} s"
        else:
            speed, over = recorded, ""
        if not speed or speed <= limit:
            return None
        return (
            f"Top speed{over} {speed:.1f} km/h is more than is plausible for {a.activity_type} ({limit:g} km/h)",
            {"rule": "max_speed_above_limit", "max_speed_kmh": round(speed, 1), "limit_kmh": limit,
             "recorded_max_speed_kmh": round(recorded, 1), "from_streams": streams is not None,
             "activity_type": a.activity_type},
            "high" if speed >= 2 * limit else "medium",
        )

    def _sustained(self, a):
        table = _sustained_table(self.config).get(a.activity_type)
        if not table or a.is_trainer or a.is_indoor:
            return None
        streams = self.streams(a)
        if streams is None:
            return None  # not fetched (yet), or none exist
        seconds = float(self.config.get("sustained_seconds", DEFAULT_SUSTAINED_SECONDS))
        loosest = max(kmh for _, kmh in table)
        limit_at = Interpolator(table)
        # Each window stands for the time until the next one starts (a few
        # seconds); adding those up for the too-fast windows gives about how
        # long it was too fast. "From" is the middle of the first such window.
        ignored = float(self.config.get("grade_ignored_first_seconds", DEFAULT_GRADE_IGNORED_FIRST_SECONDS))
        ws = list(windows(streams, seconds, grade_after_seconds=ignored))
        worst, first, too_fast_seconds = None, None, 0.0
        for index, w in enumerate(ws):
            # Without a trustworthy grade (no altitude, the first minutes, or
            # too short a stretch) only the most generous limit applies.
            limit = limit_at(w.grade_percent) if w.grade_percent is not None else loosest
            if w.speed_kmh <= limit:
                continue
            first = first or w
            too_fast_seconds += (ws[index + 1].start - w.start) if index + 1 < len(ws) else 0.0
            ratio = w.speed_kmh / limit
            if worst is None or ratio > worst[0]:
                worst = (ratio, w, limit)
        if worst is None:
            return None
        ratio, w, limit = worst
        grade = f"{w.grade_percent:+.0f}% grade" if w.grade_percent is not None else "unknown grade"
        summary = (f"{w.speed_kmh:.0f} km/h for {_duration(w.end - w.start)} at {grade}, {clock(w.start)} "
                   f"into the activity (plausible: up to {limit:.0f} km/h there)")
        first_at = clock((first.start + first.end) / 2)
        if too_fast_seconds > (w.end - w.start) + 1:
            summary += f", too fast for about {_duration(too_fast_seconds)} in total from {first_at}"
        return (
            summary,
            {"rule": "sustained_speed_above_limit", "speed_kmh": round(w.speed_kmh, 1),
             "grade_percent": None if w.grade_percent is None else round(w.grade_percent, 1),
             "limit_kmh": round(limit, 1), "at": clock(w.start), "window_seconds": round(w.end - w.start),
             "too_fast_from": first_at, "too_fast_seconds": round(too_fast_seconds),
             "activity_type": a.activity_type},
            "high" if ratio >= 1.5 else "medium",
        )


def _duration(seconds: float) -> str:
    if seconds < 120:
        return f"{int(round(seconds))} s"
    minutes = int(round(seconds / 60))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"
