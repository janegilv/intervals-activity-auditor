"""Report files written after each run, organised by activity:
report.html (open in a browser), report.md (read it), report.json (scripts)
and report.csv (spreadsheets). They include open and acknowledged issues;
resolved ones are left out."""
from pathlib import Path

from intervals_auditor.reporting import csv_report, html_report, json_report, markdown_report
from intervals_auditor.views import ActivityView


def write_reports(views: list[ActivityView], out_dir: Path, generated_at: str,
                  meta: dict | None = None) -> list[Path]:
    """Write all reports; returns their paths, report.html first."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / "report.html", out_dir / "report.md", out_dir / "report.json", out_dir / "report.csv"]
    html_report.write(views, paths[0], generated_at, meta)
    markdown_report.write(views, paths[1], generated_at)
    json_report.write(views, paths[2], generated_at)
    csv_report.write(views, paths[3], generated_at)
    return paths
