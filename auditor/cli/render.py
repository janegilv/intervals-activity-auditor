"""Plain-text rendering for the CLI. Kept simple on purpose; iterate here."""
from auditor.formatting import activity_heading, format_details
from auditor.models import Acknowledgment, activity_url
from auditor.services.queries import ActivityDetail, StatusInfo
from auditor.services.runner import RunResult
from auditor.timeutil import format_local
from auditor.views import OPEN, RESOLVED, ActivityView, IssueView


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def issue_line(issue: IssueView, *, with_state: bool = False) -> str:
    state = f"{issue.state.upper():<12} " if with_state else ""
    return f"{state}[{issue.severity.upper()}] {issue.check}: {issue.summary}"


def ack_line(ack: Acknowledgment) -> str:
    note = f": {ack.note}" if ack.note else ""
    return f"acknowledged #{ack.id} {format_local(ack.created_at)}{note}"


def activity_views(views: list[ActivityView]) -> str:
    blocks = []
    for view in views:
        lines = [activity_heading(view.activity, view.activity_id), f"  {view.activity_id}  {view.url}"]
        for issue in view.issues:
            lines.append(f"  {issue_line(issue)}")
            if issue.ack is not None:
                lines.append(f"      {ack_line(issue.ack)}")
            elif issue.reopen_reason:
                lines.append(f"      reopened: {issue.reopen_reason}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def issues_summary(views: list[ActivityView], *, acknowledged_hidden: int = 0) -> str:
    open_count = sum(len(v.issues_in(OPEN)) for v in views)
    text = f"{_plural(open_count, 'open issue', 'open issues')} on {_plural(len(views), 'activity', 'activities')}"
    if acknowledged_hidden:
        text += f" ({acknowledged_hidden} acknowledged not shown, use --all)"
    return text


def activity_detail(detail: ActivityDetail) -> str:
    a = detail.activity
    snapshot = next((ack.snapshot.get(detail.activity_id) for ack in detail.acks
                     if detail.activity_id in ack.snapshot), None)
    lines = [activity_heading(a, detail.activity_id, snapshot), f"{detail.activity_id}  {activity_url(detail.activity_id)}"]
    if a is not None:
        raw = a.raw
        lines.append(f"Source {raw.get('source') or '-'} · Strava id {raw.get('strava_id') or '-'} · "
                     f"device {raw.get('device_name') or '-'}")
        lines.append(f"First seen {format_local(a.first_seen_at)} · last changed {format_local(a.updated_at)} · "
                     f"synced {format_local(a.synced_at)} · fingerprint {a.fingerprint}")
    else:
        lines.append("This activity is no longer in Intervals.icu (deleted).")

    lines += ["", "Issues:"]
    if not detail.issues:
        lines.append("  none")
    for issue in detail.issues:
        lines.append(f"  {issue_line(issue, with_state=True)}")
        when = f"since {format_local(issue.first_seen_at)}"
        if issue.state == RESOLVED:
            when = f"resolved {format_local(issue.resolved_at)}"
        elif issue.notified_at:
            when += f" · notified {format_local(issue.notified_at)}"
        lines.append(f"      {when}")
        if issue.reopen_reason and issue.state != RESOLVED:
            lines.append(f"      reopened: {issue.reopen_reason}")
        for rid, url in issue.related_urls:
            lines.append(f"      related: {rid}  {url}")
        for detail_line in format_details(issue.details):
            lines.append(f"      - {detail_line}")
        if issue.ack is not None:
            lines.append(f"      {ack_line(issue.ack)}")

    lines += ["", "Acknowledgments:"]
    if not detail.acks:
        lines.append("  none")
    for ack in detail.acks:
        lines.append("  " + ack_summary(ack))
    return "\n".join(lines)


def ack_summary(ack: Acknowledgment) -> str:
    related = f" (with {', '.join(ack.related_activity_ids)})" if ack.related_activity_ids else ""
    state = "active " if ack.is_active else "revoked"
    text = f"#{ack.id} {state} {format_local(ack.created_at)}  {ack.activity_id} {ack.check}{related}"
    if ack.note:
        text += f"  note: {ack.note}"
    if not ack.is_active:
        text += f"\n      revoked {format_local(ack.revoked_at)}: {ack.revoked_reason}"
    return text


def acks_list(acks: list[Acknowledgment]) -> str:
    blocks = []
    for ack in acks:
        snapshot = ack.snapshot.get(ack.activity_id)
        lines = [ack_summary(ack),
                 f"      {activity_heading(None, ack.activity_id, snapshot) if snapshot else ack.activity_id}",
                 f"      {ack.url}"]
        for rid in ack.related_activity_ids:
            related = ack.snapshot.get(rid)
            if related:
                lines.append(f"      related: {activity_heading(None, rid, related)}  {related.get('url')}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def run_summary(result: RunResult) -> str:
    lines = []
    if result.sync:
        s = result.sync
        lines.append(f"Synced {'full history' if s.full else 'recent activities'}: {s.fetched} fetched, "
                     f"{len(s.inserted)} new, {len(s.changed)} changed, {len(s.removed)} removed")
        if s.prune_skipped:
            lines.append(f"WARNING: {s.prune_skipped}")
    elif result.sync_error:
        lines.append(f"Sync FAILED ({result.sync_error}); showing stored data")
    elif result.offline:
        lines.append("Offline: using stored data only")
    if result.strava and (result.strava.enriched or result.strava.not_found or result.strava.rate_limited):
        st = result.strava
        lines.append(f"Strava: {len(st.enriched)} enriched, {len(st.not_found)} not found"
                     + (f", rate limited ({st.remaining} left for next run)" if st.rate_limited else ""))
    if result.strava_error:
        lines.append(f"Strava FAILED: {result.strava_error}")
    if result.audit:
        au = result.audit
        lines.append(f"Checks: {', '.join(f'{k} {v}' for k, v in au.counts.items()) or 'none enabled'}")
        lines.append(f"Changes since last run: {len(au.new)} new, {len(au.reopened)} reopened, "
                     f"{len(au.resolved)} resolved, {len(au.revoked_acks)} acknowledgment(s) revoked")
    return "\n".join(lines)


def status(info: StatusInfo, settings) -> str:
    lines = [
        f"Database        {settings.db_path}",
        f"Activities      {info.activity_count}",
        f"Issues          {info.open_issues} open · {info.acknowledged_issues} acknowledged · "
        f"{info.resolved_issues} resolved",
        f"Last sync       {format_local(info.last_sync)} (full history: {format_local(info.last_full_sync)})",
    ]
    if info.sync_failing_since:
        lines.append(f"SYNC FAILING    since {format_local(info.sync_failing_since)}: {info.sync_last_error}")
    lines += [
        f"Notifiers       {', '.join(settings.notifier_names) or 'none (set NOTIFIERS in .env)'}",
        f"Notify after    {settings.notify_after_minutes:g} minutes",
        f"Log file        {settings.log_file}",
        "",
        "Recent runs:",
    ]
    for run in info.recent_runs:
        audit = run.summary.get("audit") or {}
        extra = f"{audit.get('new', 0)} new, {audit.get('resolved', 0)} resolved" if audit else ""
        err = f"  {run.error}" if run.error else ""
        lines.append(f"  #{run.id} {format_local(run.started_at)} {run.command:<8} {run.status:<8} {extra}{err}")
    if not info.recent_runs:
        lines.append("  none yet")
    return "\n".join(lines)
