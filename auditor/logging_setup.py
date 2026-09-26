"""Logging configuration shared by the CLI and the monitor.

Two destinations:

- the console (stderr), whose level depends on the command -- chatty for
  sync/monitor runs, quiet for query commands -- and on -v/-q/--log-level;
- a rotating log file (data/logs/auditor.log by default) that always
  records what the application did, at LOG_LEVEL (INFO unless changed).

Levels used across the app:

  ERROR    something failed (sync, a notifier, a Strava lookup)
  WARNING  something was skipped or looks suspicious (rate limit, prune
           guard, no notifiers configured)
  INFO     what a run did: counts, new/resolved issues, notifications sent
  DEBUG    per-activity detail: fingerprint changes, HTTP calls, payload
           summaries
  TRACE    full raw payloads (very verbose)
"""
import logging
import logging.handlers
import sys
from pathlib import Path

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

LEVEL_NAMES = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR")

CONSOLE_FORMAT = "%(levelname)-7s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def level_from_name(name: str | None, default: int = logging.INFO) -> int:
    if not name:
        return default
    value = logging.getLevelName(name.strip().upper())
    return value if isinstance(value, int) else default


def configure_logging(
    *,
    console_level: int = logging.INFO,
    file_level: int = logging.INFO,
    log_file: Path | None = None,
    max_bytes: int = 1_000_000,
    backup_count: int = 5,
) -> None:
    root = logging.getLogger("auditor")
    root.handlers.clear()
    root.propagate = False
    root.setLevel(min(console_level, file_level) if log_file else console_level)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
        root.addHandler(file_handler)
