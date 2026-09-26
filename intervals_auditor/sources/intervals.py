"""Read-only Intervals.icu API client (personal API key authentication)."""
import logging
import time
from datetime import date

import requests

BASE_URL = "https://intervals.icu/api/v1"

log = logging.getLogger(__name__)


class IntervalsClient:
    def __init__(self, api_key: str, athlete_id: str, timeout: int = 30):
        self.api_key = api_key
        self.athlete_id = athlete_id
        self.timeout = timeout

    def list_activities(self, oldest: date, newest: date) -> list[dict]:
        url = f"{BASE_URL}/athlete/{self.athlete_id}/activities"
        params = {"oldest": oldest.isoformat(), "newest": newest.isoformat()}
        started = time.monotonic()
        response = requests.get(url, params=params, auth=("API_KEY", self.api_key), timeout=self.timeout)
        log.debug("GET %s %s -> %s (%d bytes, %.1fs)", url, params, response.status_code,
                  len(response.content), time.monotonic() - started)
        response.raise_for_status()
        return response.json()

    def get_streams(self, activity_id: str, types: tuple[str, ...]) -> list | dict | None:
        """The activity's streams (time series) of the given types, or None
        if Intervals.icu has none for it (e.g. an activity that came from
        Strava, whose data Intervals.icu can't hand out)."""
        url = f"{BASE_URL}/activity/{activity_id}/streams.json"
        params = {"types": ",".join(types)}
        started = time.monotonic()
        response = requests.get(url, params=params, auth=("API_KEY", self.api_key), timeout=self.timeout)
        log.debug("GET %s %s -> %s (%d bytes, %.1fs)", url, params, response.status_code,
                  len(response.content), time.monotonic() - started)
        if response.status_code == 429:
            raise IntervalsRateLimitError(response.headers.get("Retry-After") or "too many requests")
        if response.status_code in (404, 422):
            return None
        response.raise_for_status()
        return response.json()


class IntervalsRateLimitError(RuntimeError):
    pass
