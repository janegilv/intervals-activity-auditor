from .base import ActivityCheck

# Activity types where a genuine outdoor session is expected to carry GPS
# distance. Type alone doesn't distinguish "should have GPS but doesn't"
# from "no GPS expected" though -- an indoor trainer "Ride" is still a
# "Ride" -- so is_trainer/is_indoor are checked separately below.
DEFAULT_OUTDOOR_TYPES = [
    "Ride", "GravelRide", "MountainBikeRide", "Hike", "Run", "TrailRun",
    "Walk", "NordicSki", "AlpineSki", "OpenWaterSwim", "IceSkate",
]


class MissingDistanceCheck(ActivityCheck):
    name = "missing_distance"
    description = (
        "Finds outdoor activities with no recorded GPS distance -- usually a "
        "broken export or a sync gap between Garmin/Strava and Intervals.icu, "
        "not a real zero-distance workout."
    )

    def run(self, activities):
        outdoor_types = set(self.config.get("outdoor_types", DEFAULT_OUTDOOR_TYPES))
        min_moving = int(self.config.get("minimum_moving_seconds", 60))

        findings = []
        for a in activities:
            if a.activity_type not in outdoor_types:
                continue
            if a.is_trainer or a.is_indoor:
                # An indoor trainer/treadmill session of an otherwise-outdoor
                # type (e.g. a "Ride" on a smart trainer) legitimately has no
                # GPS distance -- that's expected, not a data problem.
                continue
            if (a.moving_time or 0) < min_moving:
                # Too short for a missing distance to mean much either way
                # (e.g. a few-second stub or false start).
                continue
            if a.distance:
                continue

            findings.append(self.finding(
                f"{a.activity_type} activity has no recorded distance",
                [a],
                [{
                    "rule": "missing_distance",
                    "activity_type": a.activity_type,
                    "moving_time_seconds": a.moving_time,
                }],
                "medium",
            ))
        return findings
