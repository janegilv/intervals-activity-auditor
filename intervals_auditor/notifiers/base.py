"""Notifier plugin interface and message rendering.

A notifier is a small class with a `name`, a `from_env()` constructor that
reads its settings (and raises ConfigError if something is missing), and a
`send()` that delivers a Notification or raises. Register it in
notifiers/__init__.py and enable it with NOTIFIERS=<name> in .env.

A Notification is structured (title, summary, one item per activity) so
each service can render it the way it displays best: HTML for Pushover,
Markdown for Discord, plain text for the console.
"""
import html
import re
from dataclasses import dataclass, field


@dataclass
class NotificationItem:
    heading: str             # e.g. "2026-09-20 07:02 · Ride · Morning Ride · 1h02m · 30.1 km"
    url: str | None = None   # link to the activity
    link_text: str | None = None  # e.g. the activity id
    lines: list[str] = field(default_factory=list)


@dataclass
class Notification:
    title: str
    summary: str
    items: list[NotificationItem] = field(default_factory=list)
    footer: str | None = None
    priority: int = 0  # -1 quiet, 0 normal, 1 high (services that support it)


class NotifierError(RuntimeError):
    pass


class Notifier:
    name = "base"

    @classmethod
    def from_env(cls, env) -> "Notifier":
        return cls()

    def send(self, notification: Notification) -> None:
        raise NotImplementedError


def _fit(parts_head: list[str], blocks: list[str], parts_tail: list[str], max_chars: int | None,
         more_text) -> str:
    """Join head + as many item blocks as fit + tail, adding an "and N more"
    line when items had to be dropped."""
    def join(chosen, dropped):
        extra = [more_text(dropped)] if dropped else []
        return "\n\n".join(p for p in parts_head + chosen + extra + parts_tail if p)

    if max_chars is None:
        return join(blocks, 0)
    chosen = []
    for block in blocks:
        if len(join(chosen + [block], len(blocks) - len(chosen) - 1)) > max_chars:
            break
        chosen.append(block)
    text = join(chosen, len(blocks) - len(chosen))
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _more(dropped: int) -> str:
    return f"…and {dropped} more activit{'y' if dropped == 1 else 'ies'}."


def render_text(n: Notification, max_chars: int | None = None) -> str:
    blocks = []
    for item in n.items:
        lines = [item.heading]
        if item.url:
            lines.append(f"{item.link_text or ''} {item.url}".strip())
        lines += [f"  - {line}" for line in item.lines]
        blocks.append("\n".join(lines))
    return _fit([n.title, n.summary], blocks, [n.footer or ""], max_chars, _more)


def shorten(text: str, max_chars: int | None) -> str:
    if max_chars is None or len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def render_html(n: Notification, max_chars: int | None = None, max_line_chars: int | None = None) -> str:
    """Pushover's HTML subset: <b>, <i>, <u>, <font>, <a href>; newlines as-is.
    max_line_chars shortens each issue line so more activities fit."""
    def e(text):  # element text; quotes only need escaping inside attributes
        return html.escape(text, quote=False)

    blocks = []
    for item in n.items:
        lines = [f"<b>{e(item.heading)}</b>"]
        if item.url:
            lines.append(f'<a href="{html.escape(item.url)}">{e(item.link_text or item.url)}</a>')
        lines += [f"• {e(shorten(line, max_line_chars))}" for line in item.lines]
        blocks.append("\n".join(lines))
    return _fit([e(n.summary)], blocks, [f"<i>{e(n.footer)}</i>" if n.footer else ""], max_chars, _more)


_MARKDOWN_SPECIAL = re.compile(r"([\\*_~`|>\[\]])")


def escape_markdown(text: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def render_markdown(n: Notification, max_chars: int | None = None) -> str:
    m = escape_markdown
    blocks = []
    for item in n.items:
        lines = [f"**{m(item.heading)}**"]
        if item.url:
            lines.append(f"[{m(item.link_text or 'Open in Intervals.icu')}]({item.url})")
        lines += [f"- {m(line)}" for line in item.lines]
        blocks.append("\n".join(lines))
    return _fit([m(n.summary)], blocks, [f"*{m(n.footer)}*" if n.footer else ""], max_chars, _more)
