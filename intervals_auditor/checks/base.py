from intervals_auditor.models import Activity, Issue


def describe(activity: Activity) -> str:
    """Short way to refer to another activity inside an issue summary."""
    return f'{activity.id} "{activity.name}" ({activity.activity_type}, {activity.local_start})'


class ActivityCheck:
    """A rule applied to activities. run() gets every activity (so a check
    can compare activities with each other) and returns one Issue per
    problem per activity."""

    name = "base"
    description = ""

    def __init__(self, config=None, streams=None):
        self.config = config or {}
        # streams(activity) -> Streams | None: the activity's stored time
        # series, for the types stream_activity_types() asks for.
        self.streams = streams or (lambda activity: None)

    @classmethod
    def validate_config(cls, config: dict) -> None:
        """Raise ValueError if the settings can't work (checked before a
        change from the web interface is saved)."""

    @classmethod
    def stream_activity_types(cls, config: dict) -> set[str]:
        """Activity types this check needs streams (time series) for; they're
        fetched from Intervals.icu during a sync. Most checks need none."""
        return set()

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
