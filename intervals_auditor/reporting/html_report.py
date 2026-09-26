"""report.html: a self-contained page to open in any browser (no server,
no internet needed). The data is embedded as JSON and a small script adds
search, filters, sorting and "copy ack command" buttons."""
import json
from pathlib import Path

from intervals_auditor.checks import CHECKS
from intervals_auditor.reporting.json_report import activity_to_dict
from intervals_auditor.views import ActivityView

TEMPLATE = Path(__file__).with_name("report_template.html")
PLACEHOLDER = "/*__DATA__*/"
DEFAULT_COMMAND = "python main.py"


def render(views: list[ActivityView], generated_at: str, meta: dict | None = None) -> str:
    meta = meta or {}
    data = {
        "generated_at": generated_at,
        "last_sync": meta.get("last_sync"),
        "command": meta.get("command") or DEFAULT_COMMAND,
        "checks": {name: cls.description for name, cls in CHECKS.items()},
        "activities": [activity_to_dict(v) for v in views],
    }
    # Embedded in a <script> element: escape "<" so no "</script>" or "<!--"
    # in an activity name can break out of it (JSON.parse turns it back).
    encoded = json.dumps(data, default=str, ensure_ascii=False).replace("<", "\\u003c")
    return TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, encoded)


def write(views: list[ActivityView], path: Path, generated_at: str, meta: dict | None = None):
    path.write_text(render(views, generated_at, meta), encoding="utf-8")
