import logging
import time

import requests

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"


class StravaAuthError(RuntimeError):
    pass


class StravaRateLimitError(RuntimeError):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        msg = "Strava API rate limit hit"
        if retry_after:
            msg += f" (retry after {retry_after}s)"
        super().__init__(msg)


class StravaClient:
    """Minimal read-only Strava API v3 client: refreshes its own access token
    from a long-lived refresh token and fetches single activities by id.

    No write/delete endpoints are used or exposed, matching this project's
    read-only design.
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, timeout: int = 30,
                 on_refresh_token=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.timeout = timeout
        # Called with the new refresh token whenever Strava rotates it, so
        # the caller can store it (the old one stops working immediately).
        self.on_refresh_token = on_refresh_token
        self._access_token = None
        self._expires_at = 0

    def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        response = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=self.timeout,
        )
        if response.status_code in (400, 401):
            raise StravaAuthError(
                "Strava rejected the refresh token/credentials. "
                "Re-run strava_auth_setup.py to get a fresh STRAVA_REFRESH_TOKEN."
            )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        self._expires_at = payload["expires_at"]
        # Strava can issue a new refresh token on any refresh, and "once a new
        # refresh token is returned, the older refresh token is invalidated
        # immediately" -- so it must be kept for the next run.
        new_token = payload.get("refresh_token")
        if new_token and new_token != self.refresh_token:
            self.refresh_token = new_token
            log.info("Strava issued a new refresh token")
            if self.on_refresh_token:
                self.on_refresh_token(new_token)
        return self._access_token

    def get_activity(self, activity_id: str) -> dict | None:
        """Fetch full activity detail from Strava. Returns None if Strava has
        no such activity (404) -- e.g. it was since deleted on Strava's side."""
        token = self._ensure_token()
        response = requests.get(
            f"{API_BASE}/activities/{activity_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"include_all_efforts": "false"},
            timeout=self.timeout,
        )
        log.debug("GET Strava activity %s -> %s", activity_id, response.status_code)
        if response.status_code == 404:
            return None
        if response.status_code == 401:
            raise StravaAuthError("Strava rejected the access token when fetching an activity.")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise StravaRateLimitError(retry_after=int(retry_after) if retry_after else None)
        response.raise_for_status()
        return response.json()
