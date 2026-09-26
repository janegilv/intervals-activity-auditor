"""Pushover (https://pushover.net): push notifications to your phone.

Setup: log in at pushover.net, copy "Your User Key", then "Create an
Application/API Token" (name it e.g. "Intervals auditor") and copy its token.

  NOTIFIERS=pushover
  PUSHOVER_USER_KEY=...
  PUSHOVER_APP_TOKEN=...
  PUSHOVER_DEVICE=          # optional: only send to this device
"""
import logging

import requests

from intervals_auditor.errors import ConfigError
from intervals_auditor.notifiers.base import Notification, Notifier, NotifierError, render_html

log = logging.getLogger(__name__)

API_URL = "https://api.pushover.net/1/messages.json"
MAX_MESSAGE_CHARS = 1024
MAX_TITLE_CHARS = 250
# Push notifications are read on a phone: keep each issue to about two lines.
MAX_LINE_CHARS = 90


class PushoverNotifier(Notifier):
    name = "pushover"

    def __init__(self, app_token: str, user_key: str, device: str | None = None, timeout: int = 15):
        self.app_token = app_token
        self.user_key = user_key
        self.device = device
        self.timeout = timeout

    @classmethod
    def from_env(cls, env) -> "PushoverNotifier":
        token = (env.get("PUSHOVER_APP_TOKEN") or "").strip()
        user = (env.get("PUSHOVER_USER_KEY") or "").strip()
        if not token or not user:
            raise ConfigError("The pushover notifier needs PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY in .env.")
        return cls(token, user, (env.get("PUSHOVER_DEVICE") or "").strip() or None)

    def build_payload(self, n: Notification) -> dict:
        data = {
            "token": self.app_token,
            "user": self.user_key,
            "title": n.title[:MAX_TITLE_CHARS],
            "message": render_html(n, MAX_MESSAGE_CHARS, MAX_LINE_CHARS),
            "html": 1,
            "priority": n.priority,
        }
        if len(n.items) == 1 and n.items[0].url:
            data["url"] = n.items[0].url
            data["url_title"] = "Open in Intervals.icu"
        if self.device:
            data["device"] = self.device
        return data

    def send(self, n: Notification) -> None:
        response = requests.post(API_URL, data=self.build_payload(n), timeout=self.timeout)
        log.debug("Pushover responded %s: %s", response.status_code, response.text[:200])
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or body.get("status") != 1:
            errors = "; ".join(body.get("errors", [])) or response.text[:200]
            raise NotifierError(f"Pushover rejected the message (HTTP {response.status_code}): {errors}")
