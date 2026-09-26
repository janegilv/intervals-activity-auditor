from datetime import date
import requests

BASE_URL = "https://intervals.icu/api/v1"

class IntervalsClient:
    def __init__(self, api_key: str, athlete_id: str, timeout: int = 30):
        self.api_key = api_key
        self.athlete_id = athlete_id
        self.timeout = timeout

    def list_activities(self, oldest: date, newest: date):
        # Intervals.icu personal API key authentication.
        url = f"{BASE_URL}/athlete/{self.athlete_id}/activities"
        params = {
            "oldest": oldest.isoformat(),
            "newest": newest.isoformat(),
        }
        response = requests.get(
            url,
            params=params,
            auth=("API_KEY", self.api_key),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()
