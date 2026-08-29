# What I ended up doing

- Remove all garmin connect and intervals.icu that are very short and not present in strava.
- Duplicate/overlapping activities in intervals.icu:
  - Trimmed/cropped activities: Delete the garmin activities and keep the strava one.
  - Split activities: Delete the garmin activities, keep the strava ones.
  - Merged activities: Delete the garmin activities, keep the strava ones.
  - 
## More detailed information
 - Downloading trimmed activities from garmin seems to download the original activity and not the trimmed one. I have only tried exporting original from garmin, not the full email download.

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

## Strava enrichment

Some cached activities are Strava-only "stub" records: Intervals.icu has a reference to
them but can't return full data for them via its API (`"source": "STRAVA"`, no name,
type, or duration -- just a timestamp). This commonly happens when the Strava-side
activity has been cropped, split, or merged independently of the Garmin-recorded copy,
which breaks the `strava_id` link Intervals.icu stores on the original activity.

`--enrich-strava` fetches full data for these stubs directly from the Strava API and
writes it into the same local cache, so `duplicate_overlap` (and any other check) can
work with the real thing instead of guessing from a single timestamp.

One-time setup:

1. Create a free app at https://www.strava.com/settings/api (Authorization Callback
   Domain can just be `localhost`).
2. Put the app's Client ID/Secret into `.env` as `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET`.
3. Run `python strava_auth_setup.py` and follow the prompts to authorize the app in your
   browser. It prints a `STRAVA_REFRESH_TOKEN` to add to `.env`. This is a one-time step --
   the refresh token doesn't expire unless you revoke the app's access on Strava.

Then run:

```bash
python main.py --offline --enrich-strava
```

Every stub is resolved once and remembered in the cache from then on -- a successful
fetch fills in the real data, and a confirmed 404 (e.g. an activity long since deleted on
Strava) is recorded too, so it's never queried again either. Repeat runs only ever fetch
newly-appeared stubs, which keeps this well within Strava's API rate limits.

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

It also flags a zero-duration activity (a Strava-only stub -- see "Strava enrichment"
above -- that hasn't been resolved to real data) whose single timestamp falls inside
another activity's time window (`point_in_time_within_activity`), even though there's no
duration to compute a percentage from.

### under_time_limit

Enabled. Flags activities shorter than `maximum_duration_seconds` (see `checks.toml`).

Run it standalone without touching `checks.toml`:

```bash
python main.py --offline --check under_time_limit
```

### garmin_link_only

Enabled, but still under testing and development. API metadata can differ, so inspect cached `raw_json` and adapt `_is_garmin_only()` / `_strava_id_none()` in `checks/garmin_link_only.py` to match your data if the heuristic misfires.

## Add a new check

1. Create a class in `checks/`.
2. Inherit from `ActivityCheck`.
3. Implement `run(activities)`.
4. Add it to `CHECKS` in `main.py`.
5. Add configuration in `checks.toml`.

All checks return the same finding structure, so new checks automatically appear in JSON, CSV, and Markdown reports.
