from pathlib import Path

from intervals_auditor.formatting import activity_heading, format_details
from intervals_auditor.timeutil import format_local
from intervals_auditor.views import ACKNOWLEDGED, OPEN, ActivityView


def _section(lines, title, views, state):
    selected = [(v, v.issues_in(state)) for v in views]
    selected = [(v, issues) for v, issues in selected if issues]
    lines += [f"## {title}", ""]
    if not selected:
        lines += ["None.", ""]
        return
    for view, issues in selected:
        lines += [f"### {activity_heading(view.activity, view.activity_id)}", "",
                  f"`{view.activity_id}` · [Open in Intervals.icu]({view.url})", ""]
        for issue in issues:
            lines.append(f"- issue {issue.number}: **{issue.severity.upper()}** `{issue.check}` — {issue.summary}")
            for detail in format_details(issue.details):
                lines.append(f"  - {detail}")
            for rid, url in issue.related_urls:
                lines.append(f"  - related: [{rid}]({url})")
            if issue.ack is not None:
                note = f": {issue.ack.note}" if issue.ack.note else ""
                lines.append(f"  - acknowledged {format_local(issue.ack.created_at)}{note}")
        lines.append("")


def write(views: list[ActivityView], path: Path, generated_at: str):
    open_count = sum(len(v.issues_in(OPEN)) for v in views)
    open_activities = sum(1 for v in views if v.issues_in(OPEN))
    acked_count = sum(len(v.issues_in(ACKNOWLEDGED)) for v in views)
    lines = [
        "# Intervals.icu activity audit",
        "",
        f"Generated {format_local(generated_at)} · {open_count} open issue(s) on "
        f"{open_activities} activit{'y' if open_activities == 1 else 'ies'} · {acked_count} acknowledged",
        "",
    ]
    _section(lines, "Open issues", views, OPEN)
    _section(lines, "Acknowledged", views, ACKNOWLEDGED)
    path.write_text("\n".join(lines), encoding="utf-8")
