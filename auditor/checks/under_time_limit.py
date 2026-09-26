from .base import ActivityCheck

class UnderTimeLimitCheck(ActivityCheck):
    name = "under_time_limit"
    description = "Finds activities shorter than a configured duration."

    def run(self, activities):
        limit = int(self.config.get("maximum_duration_seconds", 100))
        findings = []
        for a in activities:
            if 0 < a.duration_seconds < limit:
                findings.append(self.finding(
                    f"Activity is shorter than {limit} seconds",
                    [a],
                    [{"rule": "duration_under_limit",
                      "duration_seconds": a.duration_seconds,
                      "limit_seconds": limit}],
                    "info"
                ))
        return findings
