import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auditor.app import App
from auditor.config import load_settings
from auditor.sources.strava import StravaClient


class Response:
    def __init__(self, body, status_code=200):
        self.body, self.status_code = body, status_code

    def json(self):
        return self.body

    def raise_for_status(self):
        pass


class StravaRefreshTokenTest(unittest.TestCase):
    def test_client_reports_rotated_token(self):
        seen = []
        client = StravaClient("id", "secret", "old", on_refresh_token=seen.append)
        body = {"access_token": "a", "expires_at": 9999999999, "refresh_token": "new"}
        with mock.patch("auditor.sources.strava.requests.post", return_value=Response(body)) as post:
            client._ensure_token()
            self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "old")
        self.assertEqual((client.refresh_token, seen), ("new", ["new"]))

    def test_app_keeps_latest_token_until_env_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "checks.toml").write_text("")
            env = {"STRAVA_CLIENT_ID": "id", "STRAVA_CLIENT_SECRET": "s", "STRAVA_REFRESH_TOKEN": "from-env"}
            with App(load_settings(root, env)) as app:
                self.assertEqual(app.strava_client().refresh_token, "from-env")
                app.store_strava_refresh_token("rotated")
            with App(load_settings(root, env)) as app:
                self.assertEqual(app.strava_client().refresh_token, "rotated")
            with App(load_settings(root, dict(env, STRAVA_REFRESH_TOKEN="re-authorized"))) as app:
                self.assertEqual(app.strava_client().refresh_token, "re-authorized")


if __name__ == "__main__":
    unittest.main()
