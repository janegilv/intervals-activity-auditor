from auditor.models import Activity, Issue


def describe(activity: Activity) -> str:
    """Short way to refer to another activity inside an issue summary."""
    return f'{activity.id} "{activity.name}" ({activity.activity_type}, {activity.local_start})'


class ActivityCheck:
    """A rule applied to activities. run() gets every activity (so a check
    can compare activities with each other) and returns one Issue per
    problem per activity."""

    name = "base"
    description = ""

    def __init__(self, config=None):
        self.config = config or {}

    def run(self, activities: list[Activity]) -> list[Issue]:
        raise NotImplementedError

    def issue(self, activity: Activity, summary: str, details: list[dict] | None = None,
              severity: str = "info", related: list[Activity] = ()) -> Issue:
        return Issue(
            activity_id=activity.id,
            check=self.name,
            severity=severity,
            summary=summary,
            related_activity_ids=tuple(sorted(r.id for r in related)),
            details=list(details or []),
        )
