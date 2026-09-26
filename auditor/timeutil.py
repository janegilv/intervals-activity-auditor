"""Small time helpers so every timestamp the application stores has the same
shape: timezone-aware UTC, ISO 8601, whole seconds. Keeping one format means
stored timestamps can be compared as strings and parsed back reliably."""
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp as stored by this app or returned by the APIs.
    Accepts a trailing "Z"; a naive value is treated as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_local(value: str | datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Render a stored UTC timestamp in the machine's local timezone."""
    dt = parse_iso(value) if isinstance(value, str) else value
    if dt is None:
        return "-"
    return dt.astimezone().strftime(fmt)
