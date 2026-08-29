from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

@dataclass
class Activity:
    id: str
    name: str
    activity_type: str
    start_date_local: str | None
    start_date: str | None
    moving_time: int | None
    elapsed_time: int | None
    distance: float | None
    raw: dict[str, Any]
    cached_at: str | None = None

    @property
    def duration_seconds(self) -> int:
        return int(self.elapsed_time or self.moving_time or 0)

    @property
    def start(self) -> datetime | None:
        value = self.start_date or self.start_date_local
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @property
    def end(self) -> datetime | None:
        start = self.start
        return start + timedelta(seconds=self.duration_seconds) if start else None

    @property
    def url(self) -> str:
        return f"https://intervals.icu/activities/{self.id}"
