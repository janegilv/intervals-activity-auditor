"""Discord channel webhook.

For a personal channel: in Discord choose "Add a Server" -> "Create My Own"
-> "For me and my friends" (only you are in it), then open the channel's
settings -> Integrations -> Webhooks -> New Webhook -> Copy Webhook URL.

  NOTIFIERS=discord
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
"""
import logging

import requests

from intervals_auditor.errors import ConfigError
from intervals_auditor.notifiers.base import Notification, Notifier, NotifierError, render_markdown

log = logging.getLogger(__name__)

MAX_DESCRIPTION_CHARS = 4000
COLORS = {-1: 0x95A5A6, 0: 0xE67E22, 1: 0xE74C3C}


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(self, webhook_url: str, timeout: int = 15):
        self.webhook_url = webhook_url
        self.timeout = timeout

    @classmethod
    def from_env(cls, env) -> "DiscordNotifier":
        url = (env.get("DISCORD_WEBHOOK_URL") or "").strip()
        if not url:
            raise ConfigError("The discord notifier needs DISCORD_WEBHOOK_URL in .env.")
        return cls(url)

    def build_payload(self, n: Notification) -> dict:
        return {
            "username": "Activity auditor",
            "allowed_mentions": {"parse": []},
            "embeds": [{
                "title": n.title[:256],
                "description": render_markdown(n, MAX_DESCRIPTION_CHARS),
                "color": COLORS.get(n.priority, COLORS[0]),
            }],
        }

    def send(self, n: Notification) -> None:
        response = requests.post(self.webhook_url, json=self.build_payload(n), timeout=self.timeout)
        log.debug("Discord responded %s", response.status_code)
        if response.status_code >= 300:
            raise NotifierError(f"Discord returned HTTP {response.status_code}: {response.text[:200]}")
