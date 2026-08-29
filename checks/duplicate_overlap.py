from .base import ActivityCheck
import ipdb

class DuplicateOverlapCheck(ActivityCheck):
    name = "duplicate_overlap"
    description = "Finds activities with substantial temporal overlap and similar characteristics."

    def run(self, activities):
        findings = []
        min_overlap = float(self.config.get("minimum_overlap_percent", 90.0))
        max_start_diff = int(self.config.get("maximum_start_difference_seconds", 300))
        max_duration_diff = float(self.config.get("maximum_duration_difference_percent", 10.0))
        require_same_type = bool(self.config.get("require_same_type", True))
        ignore_activities = list(self.config.get("ignore_activities", []))

        ordered = [a for a in activities if a.start and a.end and a.duration_seconds > 0]
        ordered.sort(key=lambda a: a.start)

        for i, a in enumerate(ordered):
            if a.id in ignore_activities:
              continue

            #import sys
            #import pprint

            for b in ordered[i + 1:]:
                #if a.id=="REDACTED-ACTIVITY-ID" and b.id=="REDACTED-ACTIVITY-ID":
                #  ipdb.set_trace()

                if b.start >= a.end:
                    break
                if require_same_type and a.activity_type != b.activity_type:
                    continue

                overlap = max(0, (min(a.end, b.end) - max(a.start, b.start)).total_seconds())
                shorter = min(a.duration_seconds, b.duration_seconds)
                if not shorter:
                    continue
                overlap_pct = overlap / shorter * 100
                if overlap_pct < min_overlap:
                    continue

                start_diff = abs((a.start - b.start).total_seconds())
                duration_diff = abs(a.duration_seconds - b.duration_seconds)
                duration_diff_pct = duration_diff / max(a.duration_seconds, b.duration_seconds) * 100

                reasons = [{
                    "rule": "time_overlap",
                    "overlap_seconds": round(overlap),
                    "overlap_percent_of_shorter": round(overlap_pct, 2),
                }]

                if a.activity_type == b.activity_type:
                    reasons.append({"rule": "same_activity_type", "value": a.activity_type})
                if start_diff <= max_start_diff:
                    reasons.append({"rule": "start_time_similarity", "difference_seconds": round(start_diff)})
                if duration_diff_pct <= max_duration_diff:
                    reasons.append({
                        "rule": "duration_similarity",
                        "difference_seconds": duration_diff,
                        "difference_percent": round(duration_diff_pct, 2),
                    })

                if overlap_pct >= 98 and duration_diff_pct <= 5:
                    severity = "high"
                elif overlap_pct >= min_overlap:
                    severity = "medium"
                else:
                    severity = "low"

                findings.append(self.finding(
                    f"Activities overlap by {overlap_pct:.1f}% of the shorter activity",
                    [a, b], reasons, severity
                ))
        return findings
