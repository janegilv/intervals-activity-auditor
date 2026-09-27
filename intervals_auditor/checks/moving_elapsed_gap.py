from .base import ActivityCheck


class MovingElapsedGapCheck(ActivityCheck):
    name = "moving_elapsed_gap"
    description = (
        "Finds activities where moving time and elapsed time diverge "
        "unusually -- a large gap usually means long unpaused stops (a "
        "'forgot to pause' pattern), and a negative gap means impossible "
        "data (moving time can never exceed elapsed time)."
    )

    def run(self, activities):
        min_gap_percent = float(self.config.get("minimum_gap_percent", 30.0))
        min_gap_seconds = int(self.config.get("minimum_gap_seconds", 600))
        # Elapsed time should never be less than moving time -- moving time is
        # a subset of it by definition. A handful of activities in real data
        # come back off by exactly a second or two, which looks like a
        # rounding artifact in how Intervals.icu (or the original source)
        # computed the two fields rather than a real error, so small
        # negative gaps are tolerated rather than flagged.
        negative_tolerance = int(self.config.get("negative_gap_tolerance_seconds", 5))
        # Stopped this long is rarely a break: usually the recording was
        # saved hours after the activity ended (or started hours before it).
        major_gap = int(self.config.get("major_gap_seconds", 6 * 3600))

        issues = []
        for a in activities:
            moving, elapsed = a.moving_time, a.elapsed_time
            if not moving or not elapsed:
                continue
            gap = elapsed - moving

            if gap < -negative_tolerance:
                issues.append(self.issue(
                    a,
                    "Moving time exceeds elapsed time, which shouldn't be possible",
                    [{
                        "rule": "negative_moving_elapsed_gap",
                        "moving_time_seconds": moving,
                        "elapsed_time_seconds": elapsed,
                        "gap_seconds": gap,
                    }],
                    "medium",
                ))
                continue

            gap_percent = gap / elapsed * 100
            if gap_percent < min_gap_percent or gap < min_gap_seconds:
                continue

            issues.append(self.issue(
                a,
                f"Elapsed time exceeds moving time by {gap_percent:.0f}% "
                f"({_hours(gap)} of unpaused stopped time)",
                [{
                    "rule": "moving_elapsed_gap",
                    "moving_time_seconds": moving,
                    "elapsed_time_seconds": elapsed,
                    "gap_seconds": gap,
                    "gap_percent": round(gap_percent, 1),
                }],
                # A large gap is a heads-up, not necessarily bad data -- a
                # long summit break on a hike produces exactly this pattern
                # legitimately -- unless it's many hours.
                "medium" if gap >= major_gap else "low",
            ))
        return issues


def _hours(seconds: int) -> str:
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
