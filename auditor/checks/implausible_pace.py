from auditor import derivation
from .base import ActivityCheck

# Sane default bounds (km/h) per Intervals.icu activity type, calibrated
# against real-world data (fastest a bike computer on this account has ever
# recorded is ~45 km/h; these leave generous headroom above that). Types not
# listed here have no meaningful "average pace" to sanity-check and are
# skipped entirely (e.g. WeightTraining, Workout, Transition, Elliptical).
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


class ImplausiblePaceCheck(ActivityCheck):
    name = "implausible_pace"
    description = (
        "Finds activities whose average speed is outside a plausible range for "
        "their activity type -- usually a GPS glitch, a mislabeled activity "
        "type, or bad distance/duration data."
    )

    def run(self, activities):
        # checks.toml can override/extend individual types via
        # [implausible_pace.speed_kmh_by_type]; anything not overridden there
        # still falls back to the built-in defaults below.
        bounds = dict(DEFAULT_SPEED_BOUNDS_KMH)
        bounds.update(self.config.get("speed_kmh_by_type", {}))
        # A speed just past the line isn't the same signal as one way past
        # it -- a couple of km/h either side of a bound is well within the
        # kind of natural variation (a faster/slower-than-usual day, a bound
        # that's slightly too tight for someone's own pace range) the bounds
        # can't perfectly account for. Widen each bound by this margin before
        # deciding whether to flag at all, so only a genuinely implausible
        # reading -- not a marginal one -- gets reported.
        margin = float(self.config.get("margin_kmh", 2.0))

        issues = []
        for a in activities:
            type_bounds = bounds.get(a.activity_type)
            if not type_bounds:
                continue
            speed = derivation.average_speed_kmh(a)
            if speed is None:
                continue

            low, high = type_bounds
            if low - margin <= speed <= high + margin:
                continue

            if speed > high:
                direction, ratio = "above", speed / high
            else:
                direction, ratio = "below", (low / speed if speed > 0 else float("inf"))
            # Ratio only grades severity -- being more than 2x outside the
            # plausible range is a much stronger signal of bad data than
            # just barely missing it.
            severity = "high" if ratio >= 2 else "medium"

            issues.append(self.issue(
                a,
                f"Average speed ({speed:.1f} km/h) is implausibly {direction} the "
                f"expected range for {a.activity_type} ({low}-{high} km/h, "
                f"±{margin:g} km/h margin)",
                [{
                    "rule": "speed_out_of_range",
                    "average_speed_kmh": round(speed, 2),
                    "expected_range_kmh": [low, high],
                    "margin_kmh": margin,
                    "activity_type": a.activity_type,
                }],
                severity,
            ))
        return issues
