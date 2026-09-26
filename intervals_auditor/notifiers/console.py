"""Prints the notification instead of sending it. Used by `monitor --dry-run`,
or enable it with NOTIFIERS=console to just see messages in the log output."""
import sys

from intervals_auditor.notifiers.base import Notification, Notifier, render_text


class ConsoleNotifier(Notifier):
    name = "console"

    def __init__(self, stream=None):
        self.stream = stream

    def send(self, n: Notification) -> None:
        stream = self.stream or sys.stdout
        print(render_text(n), file=stream)
        print(file=stream)
