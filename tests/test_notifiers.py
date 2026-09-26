import io
import unittest
from unittest import mock

from auditor.errors import ConfigError
from auditor.notifiers import build_notifiers, parse_names
from auditor.notifiers.base import Notification, NotificationItem, NotifierError, render_html, render_markdown, render_text
from auditor.notifiers.console import ConsoleNotifier
from auditor.notifiers.discord import DiscordNotifier
from auditor.notifiers.pushover import MAX_MESSAGE_CHARS, PushoverNotifier


def notification(n_items=2):
    return Notification(
        title="3 activity issues to look at",
        summary="New issues on 2 activities in Intervals.icu:",
        items=[NotificationItem(heading=f"2026-09-2{i} 07:00 · Ride · Ride <{i}> & more · 1h00m · 30.0 km",
                                url=f"https://intervals.icu/activities/i{i}", link_text=f"i{i}",
                                lines=["[HIGH] duplicate_overlap: Overlaps i9 \"Copy_1\" by 99.0%"])
               for i in range(n_items)],
        footer="Acknowledge: python main.py ack <id>",
    )


class Response:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code, self._body, self.text = status_code, body, text

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class RenderTest(unittest.TestCase):
    def test_text_contains_everything(self):
        text = render_text(notification())
        for part in ("3 activity issues", "i0 https://intervals.icu/activities/i0", "[HIGH] duplicate_overlap",
                     "Acknowledge"):
            self.assertIn(part, text)

    def test_html_escapes_and_links(self):
        html = render_html(notification())
        self.assertIn("Ride &lt;0&gt; &amp; more", html)
        self.assertIn('<a href="https://intervals.icu/activities/i1">i1</a>', html)

    def test_markdown_escapes(self):
        md = render_markdown(notification())
        self.assertIn(r"Copy\_1", md)
        self.assertIn("[i0](https://intervals.icu/activities/i0)", md)

    def test_truncation_keeps_limit_and_says_how_many_more(self):
        html = render_html(notification(40), MAX_MESSAGE_CHARS)
        self.assertLessEqual(len(html), MAX_MESSAGE_CHARS)
        self.assertRegex(html, r"…and \d+ more activities\.")
        self.assertIn("Acknowledge", html)  # footer survives


class NotifierTest(unittest.TestCase):
    def test_build_from_env(self):
        self.assertEqual(parse_names(" Pushover, discord ,"), ["pushover", "discord"])
        env = {"PUSHOVER_APP_TOKEN": "a", "PUSHOVER_USER_KEY": "u", "DISCORD_WEBHOOK_URL": "https://x"}
        self.assertEqual([n.name for n in build_notifiers(["pushover", "discord"], env)], ["pushover", "discord"])
        with self.assertRaises(ConfigError):
            build_notifiers(["pushover"], {})
        with self.assertRaises(ConfigError):
            build_notifiers(["telegram"], {})

    def test_pushover_payload_and_errors(self):
        p = PushoverNotifier("token", "user", device="phone")
        data = p.build_payload(notification(1))
        self.assertEqual((data["token"], data["user"], data["html"], data["device"]), ("token", "user", 1, "phone"))
        self.assertEqual(data["url"], "https://intervals.icu/activities/i0")
        with mock.patch("auditor.notifiers.pushover.requests.post", return_value=Response(200, {"status": 1})) as post:
            p.send(notification())
            self.assertEqual(post.call_args.kwargs["data"]["title"], "3 activity issues to look at")
        with mock.patch("auditor.notifiers.pushover.requests.post",
                        return_value=Response(400, {"status": 0, "errors": ["user key is invalid"]})):
            with self.assertRaisesRegex(NotifierError, "user key is invalid"):
                p.send(notification())

    def test_discord_payload_and_errors(self):
        d = DiscordNotifier("https://discord.com/api/webhooks/x")
        body = d.build_payload(notification())
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertIn("[i1](https://intervals.icu/activities/i1)", body["embeds"][0]["description"])
        with mock.patch("auditor.notifiers.discord.requests.post", return_value=Response(204)):
            d.send(notification())
        with mock.patch("auditor.notifiers.discord.requests.post", return_value=Response(404, text="Unknown Webhook")):
            with self.assertRaisesRegex(NotifierError, "Unknown Webhook"):
                d.send(notification())

    def test_console(self):
        out = io.StringIO()
        ConsoleNotifier(out).send(notification())
        self.assertIn("i1 https://intervals.icu/activities/i1", out.getvalue())


if __name__ == "__main__":
    unittest.main()
