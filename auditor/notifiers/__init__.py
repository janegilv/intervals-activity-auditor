"""Notification services. To add one (Slack, email, ntfy, ...): write a
Notifier subclass in this package and add it to NOTIFIERS below."""
from auditor.errors import ConfigError
from auditor.notifiers.base import Notification, NotificationItem, Notifier, NotifierError
from auditor.notifiers.console import ConsoleNotifier
from auditor.notifiers.discord import DiscordNotifier
from auditor.notifiers.pushover import PushoverNotifier

NOTIFIERS = {cls.name: cls for cls in (PushoverNotifier, DiscordNotifier, ConsoleNotifier)}

__all__ = ["Notification", "NotificationItem", "Notifier", "NotifierError", "NOTIFIERS", "build_notifiers"]


def parse_names(value: str | None) -> list[str]:
    return [n.strip().lower() for n in (value or "").split(",") if n.strip()]


def build_notifiers(names: list[str], env) -> list[Notifier]:
    """Create the notifiers named in NOTIFIERS, reading their settings from env."""
    unknown = [n for n in names if n not in NOTIFIERS]
    if unknown:
        raise ConfigError(f"Unknown notifier(s) in NOTIFIERS: {', '.join(unknown)}. "
                          f"Available: {', '.join(NOTIFIERS)}")
    return [NOTIFIERS[name].from_env(env) for name in names]
