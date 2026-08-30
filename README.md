Vibecoded application to assist me in cleaning up intervals.icu, garmin and strava.
Fetches intervals.icu activities, enriches some activities from strava, runs through checks for these and prints the failed checks.
Failed checks must manually be fixed, the script is "read-only"

# What I ended up doing to fix the failed checks.

- Remove all garmin connect and intervals.icu that are very short and not present in strava.
- If doing changes to garmin activities, re-export original and add back to strava. Don't remember if you have to manually add to intervals. If cropping/merging/splitting, this should be done for all activities to keep garmin and strava equal.
- Duplicate/overlapping activities in intervals.icu:
  - Trimmed/cropped activities: Trim garmin activities and re-export to intervals via gpx. Keeps ability to export? They are merged. Better would maybe be to export and trim in external app, then fit file original in garmin would be fine for export all history.
  #- Split activities: Delete the garmin activities, keep the strava ones.
  - Merged activities: Merge the garmin activities also. keep the strava ones.

## More detailed information
 - Downloading trimmed activities from garmin seems to download the original activity and not the trimmed one. I have only tried exporting original from garmin, not the full email download.
 - Last workaround was to download original from garmin, edit it and reupload. Also reupload same file to strava, more work but seems bulletproof.

# Tools
( In order of preference? )
- https://fitfileeditor.com/ (Used for cropping)
- https://www.fitfiletools.com/
  - Don't really like the section remover.
- https://www.fitfileviewer.com/
 
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

`--full-sync` also removes any cached activity no longer present in Intervals.icu (i.e.
deleted since your last full sync) -- see "Deleting activities" below before you next
reach for deleting `intervals.sqlite` by hand.

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

If Strava rate limits you mid-run, it stops there by default -- nothing already fetched is
lost, and the remaining stubs are simply picked up next time. Add `--strava-auto-retry` to
instead wait out Strava's `Retry-After` (repeating for as long as it keeps happening) and
finish the run in one go, unattended.

Add `--strava-debug LEVEL` to see what's actually coming back from Strava as it happens,
instead of only noticing bad data later in a check's output:

- `--strava-debug 1` -- one timestamped summary line per activity (name/type/start/durations/distance).
- `--strava-debug 2` -- also dumps the full raw Strava payload per activity. A lot of
  output for a large run; redirect stdout to a file, e.g.
  `python main.py --offline --enrich-strava --strava-debug 2 > strava_debug.log`.

## Deleting activities / rebuilding the cache

Deleting `intervals.sqlite` and resyncing has been the way to make the local cache
reflect activities deleted in Intervals.icu, since `upsert_many` only ever inserts or
updates and never removes a row on its own. Two ways to avoid losing Strava enrichment
work when you do this:

- **Preferred:** just run `python main.py --full-sync`. It now prunes any cached activity
  missing from that full-history response, so deletions are picked up without ever
  wiping the file -- Strava enrichment is untouched. Every sync (not just `--full-sync`)
  also protects already-resolved Strava activities from being downgraded: Intervals.icu
  always returns the exact same permanent placeholder for a Strava-only-linked activity
  it can't fetch itself, so once we've backfilled one from Strava, a sync response that's
  still just that placeholder is skipped rather than overwriting the real data. This
  doesn't block genuine updates, though -- if Intervals.icu later has real data of its
  own for that activity (e.g. it gets linked to a matching Garmin file), that update is
  recognized as more than just the placeholder and is applied normally.
- **If you do delete the file anyway** (a corrupted cache, wanting a truly clean slate):
  run `python main.py --export-strava-cache strava_backup.json` first, delete
  `intervals.sqlite`, do your normal full resync, then
  `python main.py --import-strava-cache strava_backup.json` to restore the previously
  resolved Strava records with no re-fetching.

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

### implausible_pace

Enabled. Flags an activity whose average speed (`distance / moving_time`) falls outside
a plausible range for its activity type -- usually a GPS glitch, a wrong activity type,
or bad distance/duration data. Built-in per-type bounds live in
`checks/implausible_pace.py` (`DEFAULT_SPEED_BOUNDS_KMH`); override or add a type via
`[implausible_pace.speed_kmh_by_type]` in `checks.toml`. Types with no meaningful pace
(`WeightTraining`, `Workout`, `Transition`, `Elliptical`, ...) are skipped automatically.

Each bound is widened by `margin_kmh` (2.0 km/h by default) before anything is flagged,
so a reading only a km/h or two past the line -- ordinary variation, not bad data -- is
left alone. Lower `margin_kmh` in `checks.toml` for tighter sensitivity, or raise it if
even the widened default still feels too eager.

### missing_distance

Enabled. Flags an outdoor-type activity (`Ride`, `Run`, `Hike`, ... -- see
`outdoor_types` in `checks.toml`) with no recorded GPS distance, once it's run long
enough (`minimum_moving_seconds`) that a missing distance is unlikely to be
coincidental. Indoor trainer/treadmill sessions are always excluded regardless of type
(`Activity.is_trainer` / `.is_indoor`) -- an indoor "Ride" legitimately has no GPS track,
that's not a data problem.

### moving_elapsed_gap

Disabled by default -- the "large elapsed/moving gap means unpaused stopped time" signal
turned out to be mostly noise in practice (long hike/ski breaks and similar produce it
legitimately), not a reliable indicator of bad data. It also covers a second, more
useful case: a *negative* gap (moving time exceeding elapsed time, which shouldn't be
possible) is always flagged at `medium` severity once it exceeds
`negative_gap_tolerance_seconds`. The check is left in place for that case and for
occasional manual use:

```bash
python main.py --offline --check moving_elapsed_gap
```

## Add a new check

1. Create a class in `checks/`.
2. Inherit from `ActivityCheck`.
3. Implement `run(activities)`.
4. Add it to `CHECKS` in `main.py`.
5. Add configuration in `checks.toml`.

All checks return the same finding structure, so new checks automatically appear in JSON, CSV, and Markdown reports.
