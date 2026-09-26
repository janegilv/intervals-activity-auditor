"""Command-line interface. `python main.py --help` lists the commands.

Output meant for you goes to stdout; what the application is doing goes
to the log (stderr, plus data/logs/intervals_auditor.log). Commands that sync show
INFO log lines by default; the query commands only show warnings unless
you add -v.
"""
import argparse
import logging
import sys

from intervals_auditor import __version__
from intervals_auditor.app import App
from intervals_auditor.checks import CHECKS
from intervals_auditor.cli import render
from intervals_auditor.config import load_settings
from intervals_auditor.errors import ConfigError
from intervals_auditor.lock import AlreadyRunning
from intervals_auditor.logging_setup import LEVEL_NAMES, TRACE, configure_logging, level_from_name
from intervals_auditor.notifiers import Notification, NotificationItem
from intervals_auditor.services.acks import NotFoundError
from intervals_auditor.services.audit import UnknownCheckError
from intervals_auditor.services.strava import export_strava_records, import_strava_records
from intervals_auditor.views import ACKNOWLEDGED, OPEN

log = logging.getLogger("intervals_auditor.cli")

CHATTY_COMMANDS = {"run", "monitor", "resync", "serve", "fetch-streams"}


def build_parser() -> argparse.ArgumentParser:
    def add_common(p, suppress):
        default = (lambda value: argparse.SUPPRESS) if suppress else (lambda value: value)
        p.add_argument("-v", "--verbose", action="count", default=default(0),
                       help="More log output on the console (-v debug, -vv trace).")
        p.add_argument("-q", "--quiet", action="store_true", default=default(False),
                       help="Only warnings and errors on the console.")
        p.add_argument("--log-level", choices=LEVEL_NAMES, type=str.upper, default=default(None),
                       help="Log level for both console and log file (overrides -v/-q and LOG_LEVEL).")

    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Audit Intervals.icu activities: sync, check every activity, track and acknowledge "
                    "issues, and notify about new ones. Without a command it runs `run`.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    add_common(parser, suppress=False)
    parser.add_argument("--offline", action="store_true", help="(run) Use stored data only, no API calls.")
    parser.add_argument("--only", action="append", metavar="CHECK", choices=list(CHECKS),
                        help="(run) Run only this check (even if disabled); repeatable.")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(name, help_text, epilog=None):
        p = sub.add_parser(name, help=help_text, description=help_text, epilog=epilog,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        add_common(p, suppress=True)
        return p

    p = command("run", "Sync, run all enabled checks on every activity, write reports, list open issues (default).")
    p.add_argument("--offline", action="store_true", default=argparse.SUPPRESS, help="Use stored data only.")
    p.add_argument("--only", action="append", metavar="CHECK", choices=list(CHECKS), default=argparse.SUPPRESS,
                   help="Run only this check (even if disabled); repeatable.")

    p = command("monitor", "Like run, then notify about new issues. This is what the scheduler runs.")
    p.add_argument("--offline", action="store_true", help="Use stored data only.")
    p.add_argument("--dry-run", action="store_true", help="Print the notification instead of sending it.")

    p = command("issues", "List activities with open issues (from the database, no sync).")
    p.add_argument("--all", action="store_true", help="Include acknowledged issues.")
    p.add_argument("--check", choices=list(CHECKS), help="Only issues from this check.")

    p = command("show", "Everything about one activity: issues (incl. history) and acknowledgments.")
    p.add_argument("ref", metavar="ID", help="activity id, or an issue number to show its activity")

    p = command("ack", "Acknowledge issues so they're no longer reported.", epilog="""\
examples:
  main.py ack 12 -n "two real sessions"          issue 12
  main.py ack 12 13 17 -n "..."                  several issues
  main.py ack i123456789 -n "..."                all open issues on an activity
  main.py ack i123456789 --check garmin_link_only -n "..."
  main.py ack 12 --only-this -n "..."            leave the other half of a duplicate pair alone

Issue numbers are shown after "issue:" and activity ids after "activity:"
in the `run` and `issues` output. The "i" prefix of activity ids is
optional (123456789 finds i123456789); "#12" always means issue 12.
Acknowledged issues stay hidden until an activity involved changes; list
them with `issues --all` or `acks`, and undo with `unack`.""")
    p.add_argument("refs", nargs="+", metavar="ID", help="issue number(s) and/or activity id(s)")
    p.add_argument("-n", "--note", default="", help="Why this is fine (stored with the acknowledgment).")
    p.add_argument("--check", choices=list(CHECKS),
                   help="With an activity id: only acknowledge this check's issue(s).")
    p.add_argument("--only-this", action="store_true",
                   help="Don't also acknowledge the mirrored issue on related activities (e.g. the other "
                        "half of a duplicate pair).")

    p = command("unack", "Revoke acknowledgments; the issues are reported again.", epilog="""\
examples:
  main.py unack 12
  main.py unack i123456789
  main.py unack i123456789 --check duplicate_overlap""")
    p.add_argument("refs", nargs="+", metavar="ID", help="issue number(s) and/or activity id(s)")
    p.add_argument("--check", choices=list(CHECKS), help="With an activity id: only this check's acknowledgment(s).")
    p.add_argument("--only-this", action="store_true", help="Leave related activities' acknowledgments alone.")

    p = command("acks", "List acknowledgments.")
    p.add_argument("--all", action="store_true", help="Include revoked ones (history).")
    p.add_argument("--activity", metavar="ACTIVITY_ID", help="Only those involving this activity.")

    p = command("report", "Write the reports from the database (no sync) and open report.html in your browser.")
    p.add_argument("--no-open", action="store_true", help="Only write the files, don't open the browser.")

    command("checks", "List the checks and whether they're enabled.")
    command("status", "Database, sync and notifier status, and recent runs.")

    p = command("resync", "Fetch the full history from Intervals.icu now (normally done once a day).")
    p.add_argument("--force", action="store_true",
                   help="Remove missing activities even if it's a large share (skips the safety guard).")

    command("notify-test", "Send a test message through the configured notifiers.")

    p = command("fetch-streams", "Fetch the activity streams (time series) the checks need now, "
                                 "instead of STREAMS_PER_RUN per run.")
    p.add_argument("--limit", type=int, help="At most this many activities (default: all that are waiting).")

    p = command("serve", "Run the web interface (and the scheduled monitor) until stopped.")
    p.add_argument("--host", help="Address to listen on (WEB_HOST, default 127.0.0.1; 0.0.0.0 lets other machines in).")
    p.add_argument("--port", type=int, help="Port (WEB_PORT, default 8080).")
    p.add_argument("--no-monitor", action="store_true",
                   help="Don't run the monitor on a schedule (MONITOR_INTERVAL_MINUTES) while serving.")

    p = command("strava-export", "Back up Strava-fetched activity data to a JSON file.")
    p.add_argument("path")
    p = command("strava-import", "Load a Strava backup made with strava-export.")
    p.add_argument("path")
    return parser


def setup_logging(args, settings) -> None:
    default_console = logging.INFO if args.command in CHATTY_COMMANDS else logging.WARNING
    if args.log_level:
        console_level = file_level = level_from_name(args.log_level)
    else:
        if args.verbose >= 2:
            console_level = TRACE
        elif args.verbose == 1:
            console_level = logging.DEBUG if args.command in CHATTY_COMMANDS else logging.INFO
        elif args.quiet:
            console_level = logging.WARNING
        else:
            console_level = default_console
        file_level = level_from_name(settings.log_level)
    configure_logging(console_level=console_level, file_level=file_level, log_file=settings.log_file)


# --- commands -----------------------------------------------------------

def cmd_run(app: App, args) -> int:
    with app.lock():
        result = app.runner.run(command=args.command, offline=args.offline, only=args.only,
                                full=True if args.command == "resync" else None,
                                force_prune=getattr(args, "force", False))
    print(render.run_summary(result))
    views = app.queries.activity_views(states=(OPEN,), checks=args.only)
    if views:
        print()
        print(render.activity_views(views))
    print()
    print(render.issues_summary(views))
    if result.report_paths:
        print(f"Report for your browser: {result.report_paths[0].as_uri()}  (or: {render.cli_prefix()} report)")
    if views:
        print()
        print(render.next_steps(views))
    return 1 if result.sync_error else 0


def cmd_monitor(app: App, args) -> int:
    try:
        with app.lock():
            result = app.monitor(dry_run=args.dry_run).run(offline=args.offline, dry_run=args.dry_run)
    except AlreadyRunning:
        log.info("Another run is in progress; skipping this monitor run")
        return 0
    if result.notified:
        print(f"Notified about {result.notified_issue_count} issue(s) via {', '.join(result.sent_to)}")
    for name, error in result.errors.items():
        print(f"Notifier {name} failed: {error}", file=sys.stderr)
    return 1 if (result.errors and not result.sent_to) or result.run.sync_error else 0


def cmd_issues(app: App, args) -> int:
    checks = [args.check] if args.check else None
    states = (OPEN, ACKNOWLEDGED) if args.all else (OPEN,)
    views = app.queries.activity_views(states=states, checks=checks)
    hidden = 0 if args.all else sum(len(v.issues) for v in app.queries.activity_views(states=(ACKNOWLEDGED,), checks=checks))
    if views:
        print(render.activity_views(views))
        print()
    print(render.issues_summary(views, acknowledged_hidden=hidden))
    open_views = [v for v in views if v.issues_in(OPEN)]
    if open_views:
        print()
        print(render.next_steps(open_views))
    return 0


def resolve_activity(app: App, ref: str) -> str:
    activity_id = app.queries.resolve_activity_id(ref)
    if activity_id is None:
        raise NotFoundError(f"No activity {ref!r} in the database.")
    return activity_id


def resolve_refs(app: App, refs: list[str]) -> list[tuple[str, str | int]]:
    """Issue numbers / activity ids from the command line; all must exist."""
    resolved, unknown = [], []
    for ref in refs:
        target = app.queries.resolve_ref(ref)
        (resolved if target else unknown).append(target or ref)
    if unknown:
        raise NotFoundError(f"No issue number or activity: {', '.join(unknown)}")
    return resolved


def cmd_show(app: App, args) -> int:
    kind, value = resolve_refs(app, [args.ref])[0]
    activity_id = value if kind == "activity" else app.issues.get_by_number(value).activity_id
    print(render.activity_detail(app.queries.activity_detail(activity_id)))
    return 0


def print_report_refreshed(app: App) -> None:
    paths = app.refresh_reports()
    if paths:
        print(f"Report updated; reload it in your browser: {paths[0].as_uri()}")


def cmd_ack(app: App, args) -> int:
    created = []
    for kind, value in resolve_refs(app, args.refs):
        if kind == "issue":
            created += app.ack_service.acknowledge_issue(value, note=args.note, include_related=not args.only_this)
        else:
            created += app.ack_service.acknowledge(value, check=args.check, note=args.note,
                                                   include_related=not args.only_this)
    if not created:
        print(f"Nothing to acknowledge: no unacknowledged open issues for {' '.join(args.refs)}"
              + (f" ({args.check})" if args.check else "") + ".")
        return 0
    print(f"Acknowledged {len(created)} issue(s):")
    print(render.acks_list(created))
    print()
    undo = " ".join(str(a.issue_number) for a in created)
    print(f"They're hidden until an activity involved changes. Undo with: {render.cli_prefix()} unack {undo}")
    print_report_refreshed(app)
    return 0


def cmd_unack(app: App, args) -> int:
    revoked = []
    for kind, value in resolve_refs(app, args.refs):
        if kind == "issue":
            revoked += app.ack_service.unacknowledge_issue(value, include_related=not args.only_this)
        else:
            revoked += app.ack_service.unacknowledge(value, check=args.check, include_related=not args.only_this)
    if not revoked:
        print(f"No active acknowledgments for {' '.join(args.refs)}.")
        return 0
    print(f"Revoked {len(revoked)} acknowledgment(s); the issues will be reported again:")
    print(render.acks_list(revoked))
    print()
    print_report_refreshed(app)
    return 0


def cmd_acks(app: App, args) -> int:
    activity_id = resolve_activity(app, args.activity) if args.activity else None
    acks = app.ack_service.list(include_revoked=args.all, activity_id=activity_id)
    if acks:
        print(render.acks_list(acks))
        print()
    print(f"{len(acks)} acknowledgment(s)" + ("" if args.all else " (use --all to include revoked ones)"))
    return 0


def cmd_checks(app: App, args) -> int:
    for check in app.check_settings.describe():
        mark = "✓" if check.enabled else "○"
        state = "enabled " if check.enabled else "disabled"
        print(f"{mark} {check.name:<20} {state}  {check.description}")
        changes = [f"{p.key}={p.value!r} (file: {p.file_value!r})" for p in check.params if p.changed]
        if check.enabled_changed:
            changes.insert(0, f"enabled={check.enabled}")
        if changes:
            print(f"    changed in the web interface: {', '.join(changes)}")
    return 0


def cmd_report(app: App, args) -> int:
    paths = app.runner.write_reports()
    html = paths[0]
    print(f"Wrote {', '.join(p.name for p in paths)} to {html.parent}")
    print(f"Open in a browser: {html.as_uri()}")
    if not args.no_open:
        import webbrowser
        if not webbrowser.open(html.as_uri()):
            print("Couldn't open a browser from here; open the link above yourself.", file=sys.stderr)
    return 0


def cmd_serve(settings, args) -> int:
    try:
        import uvicorn
        from intervals_auditor.web.api import create_app
    except ImportError as e:
        print(f"The web interface needs extra packages ({e.name} is missing): "
              f"pip install -r requirements-web.txt", file=sys.stderr)
        return 2
    with App(settings):
        pass  # fail fast on configuration or database problems, and upgrade the schema once
    host, port = args.host or settings.web_host, args.port or settings.web_port
    log.info("Web interface on http://%s:%d/ (API docs at /api/docs)", host, port)
    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("There is no login yet: anyone who can reach %s:%d can use the web interface", host, port)
    uvicorn.run(create_app(settings, scheduler=not args.no_monitor), host=host, port=port,
                log_level="warning", access_log=False)
    return 0


def cmd_status(app: App, args) -> int:
    print(render.status(app.queries.status(), app.settings, app.stream_status()))
    return 0


def cmd_fetch_streams(app: App, args) -> int:
    types = app.audit.stream_activity_types()
    if not types:
        print("No enabled check needs activity streams.")
        return 0
    with app.lock():
        result = app.stream_service().fetch(types, limit=args.limit)
    print(render.streams_summary(result))
    if result.rate_limited:
        print("Intervals.icu asked us to slow down; run this again later (or let the monitor continue).")
    if result.fetched:
        print(f"Run the checks with them: {render.cli_prefix()} run --offline")
    return 1 if result.errors and not result.fetched else 0


def cmd_notify_test(app: App, args) -> int:
    notifiers = app.notifiers()
    if not notifiers:
        print("No notifiers configured. Set NOTIFIERS=pushover (and its settings) in .env.", file=sys.stderr)
        return 1
    notification = Notification(
        title="Intervals auditor test",
        summary="If you can read this, notifications work.",
        items=[NotificationItem(heading="Example activity", url="https://intervals.icu/activities",
                                link_text="Intervals.icu", lines=["[INFO] example_check: nothing wrong"])],
    )
    failed = False
    for notifier in notifiers:
        try:
            notifier.send(notification)
            print(f"{notifier.name}: sent")
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"{notifier.name}: FAILED - {e}", file=sys.stderr)
    return 1 if failed else 0


def cmd_strava_export(app: App, args) -> int:
    count = export_strava_records(app.activities, args.path)
    print(f"Exported {count} Strava record(s) to {args.path}")
    return 0


def cmd_strava_import(app: App, args) -> int:
    count = import_strava_records(app.db, app.activities, args.path, clock=app.clock)
    print(f"Imported {count} Strava record(s) from {args.path}")
    return 0


COMMANDS = {
    "run": cmd_run, "resync": cmd_run, "monitor": cmd_monitor, "issues": cmd_issues, "show": cmd_show,
    "ack": cmd_ack, "unack": cmd_unack, "acks": cmd_acks, "status": cmd_status, "report": cmd_report,
    "checks": cmd_checks,
    "notify-test": cmd_notify_test, "strava-export": cmd_strava_export, "strava-import": cmd_strava_import,
    "fetch-streams": cmd_fetch_streams,
}


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.command = args.command or "run"
    for name, default in (("offline", False), ("only", None), ("dry_run", False)):
        if not hasattr(args, name):
            setattr(args, name, default)

    try:
        settings = load_settings()
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    setup_logging(args, settings)
    log.debug("Command %s with %s", args.command, vars(args))

    if args.command == "serve":
        return cmd_serve(settings, args)
    try:
        with App(settings) as app:
            return COMMANDS[args.command](app, args)
    except ConfigError as e:
        log.error("Configuration error: %s", e)
        return 2
    except (NotFoundError, UnknownCheckError) as e:
        print(str(e), file=sys.stderr)
        return 1
    except AlreadyRunning:
        print("Another run is in progress (scheduled monitor?); try again in a moment.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        log.exception("%s failed", args.command)
        return 1
