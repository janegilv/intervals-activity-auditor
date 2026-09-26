"""Report files written after each run, organised by activity:
report.md (read it), report.json (scripts), report.csv (spreadsheets).
They include open and acknowledged issues; resolved ones are left out."""
from pathlib import Path

from auditor.reporting import csv_report, json_report, markdown_report
from auditor.views import ActivityView


def write_reports(views: list[ActivityView], out_dir: Path, generated_at: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / "report.md", out_dir / "report.json", out_dir / "report.csv"]
    markdown_report.write(views, paths[0], generated_at)
    json_report.write(views, paths[1], generated_at)
    csv_report.write(views, paths[2], generated_at)
    return paths
