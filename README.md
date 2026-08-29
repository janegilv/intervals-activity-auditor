# Intervals.icu Activity Auditor

A read-only Python CLI for auditing Intervals.icu activities.

The first active check finds likely duplicate activities based primarily on temporal overlap.
The project uses a local SQLite cache so repeated runs do not need to download your full history.

## Safety

This project does **not** delete activities and contains no deletion API calls.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
INTERVALS_API_KEY=your_api_key_here
INTERVALS_ATHLETE_ID=your_athlete_id_here
```

Keep `.env` private. It is ignored by Git.

## First sync

A full initial sync is recommended:

```bash
python main.py --full-sync
```

Later, a normal run refreshes only the configured recent window:

```bash
python main.py
```

Analyse the local cache without contacting the API:

```bash
python main.py --offline
```

## Output

Each run writes:

- `output/findings.json` — canonical machine-readable output
- `output/findings.csv` — spreadsheet/script-friendly output
- `output/findings.md` — human-readable report with investigation links

## Checks

Configuration is in `checks.toml`.

### duplicate_overlap

Enabled by default. It compares overlapping activities and reports the exact reasons, including:

- overlap seconds
- overlap percentage of the shorter activity
- activity type match
- start-time difference
- duration difference

### under_time_limit

Example implementation included but disabled.

Enable it:

```toml
[under_time_limit]
enabled = true
maximum_duration_seconds = 600
```

Or run once without enabling it permanently:

```bash
python main.py --offline --check under_time_limit
```

### garmin_link_only

Example/template included but disabled. API metadata can differ, so inspect cached `raw_json` and adapt `_is_garmin_only()` in `checks/garmin_link_only.py` to match your data.

## Add a new check

1. Create a class in `checks/`.
2. Inherit from `ActivityCheck`.
3. Implement `run(activities)`.
4. Add it to `CHECKS` in `main.py`.
5. Add configuration in `checks.toml`.

All checks return the same finding structure, so new checks automatically appear in JSON, CSV, and Markdown reports.
