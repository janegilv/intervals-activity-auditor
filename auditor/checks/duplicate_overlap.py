from .base import ActivityCheck, describe


class DuplicateOverlapCheck(ActivityCheck):
    """Compares activities pairwise. A pair that overlaps produces one issue
    on each of the two activities, each naming the other as related --
    acknowledging or fixing it is then tracked per activity."""

    name = "duplicate_overlap"
    description = "Finds activities with substantial temporal overlap and similar characteristics."

    def run(self, activities):
        issues = []
        min_overlap = float(self.config.get("minimum_overlap_percent", 90.0))
        max_start_diff = int(self.config.get("maximum_start_difference_seconds", 300))
        max_duration_diff = float(self.config.get("maximum_duration_difference_percent", 10.0))
        require_same_type = bool(self.config.get("require_same_type", True))
        ignore_activities = list(self.config.get("ignore_activities", []))

        # duration_seconds >= 0 (not > 0): activities with no recorded duration are
        # kept in. These show up as source-only "stub" records (e.g. a Strava
        # activity Intervals.icu can't fetch full data for) that carry just a single
        # timestamp. A stub's timestamp landing inside a fully-recorded activity's
        # window is still a meaningful overlap worth flagging, even though there's
        # no real interval to measure a percentage of.
        ordered = [a for a in activities if a.start and a.end and a.duration_seconds >= 0]
        ordered.sort(key=lambda a: a.start)

        for i, a in enumerate(ordered):
            if a.id in ignore_activities:
                continue

            for b in ordered[i + 1:]:
                if b.start >= a.end:
                    break
                if b.id in ignore_activities:
                    continue
                if require_same_type and a.activity_type != b.activity_type:
                    continue

                overlap = max(0, (min(a.end, b.end) - max(a.start, b.start)).total_seconds())
                shorter = min(a.duration_seconds, b.duration_seconds)

                if shorter == 0:
                    # One side has no recorded duration (a point in time, not an
                    # interval) -- there's nothing to take a percentage of, so
                    # treat its timestamp falling inside the other activity's
                    # window as fully contained rather than skipping the pair.
                    point, container = (a, b) if a.duration_seconds == 0 else (b, a)
                    if not (container.start <= point.start < container.end):
                        continue
                    overlap_pct = 100.0
                    if overlap_pct < min_overlap:
                        continue
                    summaries = {
                        point.id: f"Has no recorded duration and its timestamp falls inside {describe(container)}",
                        container.id: f"Contains the timestamp of zero-duration activity {describe(point)}",
                    }
                    reasons = [{
                        "rule": "point_in_time_within_activity",
                        "details": f"{point.id} has no recorded duration; its timestamp falls inside {container.id}'s time window.",
                    }]
                else:
                    overlap_pct = overlap / shorter * 100
                    if overlap_pct < min_overlap:
                        continue
                    summaries = {
                        a.id: f"Overlaps {describe(b)} by {overlap_pct:.1f}% of the shorter activity",
                        b.id: f"Overlaps {describe(a)} by {overlap_pct:.1f}% of the shorter activity",
                    }
                    reasons = [{
                        "rule": "time_overlap",
                        "overlap_seconds": round(overlap),
                        "overlap_percent_of_shorter": round(overlap_pct, 2),
                    }]

                start_diff = abs((a.start - b.start).total_seconds())
                duration_diff = abs(a.duration_seconds - b.duration_seconds)
                longer_duration = max(a.duration_seconds, b.duration_seconds)
                duration_diff_pct = (duration_diff / longer_duration * 100) if longer_duration else 0.0

                if a.activity_type == b.activity_type:
                    reasons.append({"rule": "same_activity_type", "value": a.activity_type})
                if start_diff <= max_start_diff:
                    reasons.append({"rule": "start_time_similarity", "difference_seconds": round(start_diff)})
                if shorter > 0 and duration_diff_pct <= max_duration_diff:
                    reasons.append({
                        "rule": "duration_similarity",
                        "difference_seconds": duration_diff,
                        "difference_percent": round(duration_diff_pct, 2),
                    })

                if overlap_pct >= 98 and shorter > 0 and duration_diff_pct <= 5:
                    severity = "high"
                elif overlap_pct >= min_overlap:
                    severity = "medium"
                else:
                    severity = "low"

                issues.append(self.issue(a, summaries[a.id], reasons, severity, related=[b]))
                issues.append(self.issue(b, summaries[b.id], [dict(r) for r in reasons], severity, related=[a]))
        return issues
