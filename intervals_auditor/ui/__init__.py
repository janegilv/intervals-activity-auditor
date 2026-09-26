"""Front-end assets shared by the static report and the web interface:
base.css (styles) and issues.js (the issue browser)."""
from pathlib import Path

ASSETS = Path(__file__).resolve().parent


def asset(name: str) -> str:
    return (ASSETS / name).read_text(encoding="utf-8")
