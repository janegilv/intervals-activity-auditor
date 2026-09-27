from .base import ActivityCheck

class OverTimeLimitCheck(ActivityCheck):
    name = "over_time_limit"
    description = "Finds activities with an implausibly long duration (e.g. bad watch/API data)."

    def run(self, activities):
        limit = int(self.config.get("maximum_duration_seconds", 86400))
        issues = []
        for a in activities:
            if a.duration_seconds > limit:
                issues.append(self.issue(
                    a,
                    f"Activity duration ({a.duration_seconds}s) exceeds the plausible limit of {limit}s",
                    [{"rule": "duration_over_limit",
                      "duration_seconds": a.duration_seconds,
                      "limit_seconds": limit}],
                    "low",
                ))
        return issues
