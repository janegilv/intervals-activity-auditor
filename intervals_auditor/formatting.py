"""Human-friendly formatting shared by the CLI, reports and notifications."""
from intervals_auditor.models import Activity


def format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return "0s"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_distance(meters: float | None) -> str:
    if not meters:
        return "no distance"
    if meters < 1000:
        return f"{meters:.0f} m"
    return f"{meters / 1000:.1f} km"


def activity_heading(activity: Activity | None, activity_id: str | None = None,
                     snapshot: dict | None = None) -> str:
    """One line identifying an activity: when, what, how long, how far.
    Can also render an acknowledgment's snapshot of the activity (e.g. when
    it has since been deleted)."""
    if activity is not None:
        return (f"{activity.local_start} · {activity.activity_type or '?'} · {activity.name or '(no name)'} · "
                f"{format_duration(activity.duration_seconds)} · {format_distance(activity.distance)}")
    if snapshot:
        start = (snapshot.get("start_local") or "")[:16].replace("T", " ") or "-"
        return (f"{start} · {snapshot.get('type') or '?'} · {snapshot.get('name') or '(no name)'} · "
                f"{format_duration(snapshot.get('duration_seconds'))} · "
                f"{format_distance(snapshot.get('distance'))}")
    return f"{activity_id} (deleted)"


def format_details(details: list[dict]) -> list[str]:
    lines = []
    for detail in details:
        rule = detail.get("rule", "detail")
        rest = ", ".join(f"{k}={v}" for k, v in detail.items() if k != "rule")
        lines.append(f"{rule}: {rest}" if rest else rule)
    return lines
