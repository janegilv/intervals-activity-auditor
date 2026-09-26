Vibecoded application to assist me in cleaning up intervals.icu, garmin and strava.
Fetches intervals.icu activities, enriches some activities from strava, runs checks on every activity and keeps track of the issues it finds per activity. Issues can be acknowledged, and a scheduled monitor can notify about new ones.
Failed checks must manually be fixed, the script is "read-only"

# What I ended up doing to fix the failed checks.

-- Remove all garmin connect and intervals.icu that are very short and not present in strava.
- When changing activities:
  - Export original from garmin
  - Make necessary changes
  - Delete old garmin/strava activity
  - Add edit FIT file to garmin/strava.
    - Garmin does not send manually updated activities to strava
    - Intervals gets the activity from garmin (maybe it fetches itself?) 
  - Manual add of garmin to intervals.icu might be necessary.
- Duplicate/overlapping activities in intervals.icu:

## More detailed information
 - Downloading trimmed activities from garmin seems to download the original activity and not the trimmed one. I have only tried exporting original from garmin, not the full email download.
 - Last workaround was to download original from garmin, edit it and reupload. Also reupload same file to strava, more work but seems bulletproof.

# Tools
( In order of preference? )
- https://fitfileeditor.com/ (Used for cropping)
- https://www.fitfiletools.com/
  - Don't really like the section remover.
- https://www.fitfileviewer.com/

# Intervals Auditor

A read-only Python tool for auditing Intervals.icu activities. It keeps a
local copy of your activities, runs a set of checks on **every activity**,
and tracks the issues it finds **per activity** over time: new, resolved,
reopened, or acknowledged by you. Run it by hand from the CLI, or let the
monitor run it on a schedule and notify you (Pushover or Discord) when a
new activity has a problem.

## Safety

This project never changes anything in Intervals.icu, Garmin or Strava. It
contains no write or delete API calls; fixing an issue is always up to you.

## Setup

Requires Python 3.11+.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set at least:

```env
INTERVALS_API_KEY=your_api_key_here
INTERVALS_ATHLETE_ID=your_athlete_id_here
```

Keep `.env` private. It is ignored by Git. Every other setting has a
default; `.env.example` lists them all.

Then run it:

```bash
python main.py
```

The first run fetches your whole history; after that each run fetches the
last 30 days, plus the whole history once a day.

## Everyday use

```bash
python main.py                       # sync, run all checks, list open issues per activity
./main.py                            # the same (main.py is executable)
python main.py --offline             # same, using stored data only (no API calls)
python main.py --only missing_distance   # run just one check (also works for disabled checks)

python main.py issues                # open issues per activity, from the database (no sync)
python main.py issues --all          # ...including acknowledged ones
python main.py show i123456789       # one activity: issues, history, acknowledgments, link

python main.py ack 12 -n "two real sessions"   # acknowledge issue 12
python main.py ack 12 13 17 -n "..."           # several issues at once
python main.py ack i123456789 -n "..."         # all open issues on an activity
python main.py unack 12              # report it again
python main.py acks                  # list acknowledgments (--all includes revoked ones)

python main.py report                # open the browser report (no sync)
python main.py status                # last sync, counts, notifiers, recent runs
python main.py checks                # which checks exist and are enabled
python main.py resync                # fetch the full history now
python main.py monitor --dry-run     # what the monitor would notify, printed instead of sent
python main.py notify-test           # send a test notification
```

Every run (including the scheduled monitor) also writes reports to
`output/`, with open and acknowledged issues grouped by activity:

- `report.html`: open it in any browser (`python main.py report` does
  that). It works offline and needs nothing installed. You can search,
  filter by open/acknowledged, severity, check and activity type, sort,
  and open activities in Intervals.icu. Tick issues to get ready-made
  commands to copy: one `ack` for the open ones (with the note you type)
  and one `unack` for the acknowledged ones. Each issue also has its own
  copy button. The command prefix (`python main.py`, `./main.py`, ...) can be
  changed at the bottom of the page.
- `report.md`, `report.json` and `report.csv` (one row per activity
  issue) for reading, scripts and spreadsheets.

## How issues and acknowledgments work

- **Everything is per activity.** A check that compares activities (like
  `duplicate_overlap`) reports a pair as one issue on each of the two
  activities, each naming the other as related.
- **Issues are tracked over time.** An issue found for the first time is
  *new*; when a later run no longer finds it (you fixed or deleted the
  activity) it's *resolved*; if it comes back it's *reopened*.
- **Acknowledging** says "I've looked at this, it's fine" and hides the
  issue from `issues`, the reports' open section and notifications. It's
  stored in the database with your note, the time, and a snapshot and link
  of every activity involved, so it can be investigated later even if the
  activity is changed or deleted.
- **Every issue has a number** (`issue: 12` in the output) that stays the
  same for as long as the issue exists, and every activity has its
  Intervals.icu id (`activity: i123456789`). `ack`, `unack` and `show`
  take either, several at once. The "i" prefix of an activity id is
  optional; `#12` always means issue 12.
- `ack` with an activity id acknowledges all open issues on that activity
  (`--check` limits it to one check). For a duplicate pair, the mirrored
  issue on the other activity is acknowledged too, with the same note;
  `--only-this` leaves it alone.
- **A change to an activity revokes its acknowledgments.** Each
  acknowledgment remembers a fingerprint of the activities' start time,
  durations, distance, type, source and Garmin/Strava links. If any of that
  changes (renames and Intervals.icu's re-analysis don't count), the
  acknowledgment is revoked and the issue reopens and is notified again.
  The fields are listed in `intervals_auditor/fingerprint.py`. Revoked
  acknowledgments are kept as history (`acks --all`).
- Resolved issues and revoked acknowledgments are never deleted.

`ignore_activities` in `checks.local.toml` (see Checks) still exists for
permanently excluding an activity from `duplicate_overlap`, e.g. a test
activity you edit often.

## Monitoring on your Mac

The monitor is `python main.py monitor`: a normal run followed by
notifications. To run it every 30 minutes with launchd:

```bash
scripts/install-launchd.sh                    # every 30 min and at login
INTERVAL_MINUTES=15 scripts/install-launchd.sh  # another interval (must divide 60)
scripts/uninstall-launchd.sh
```

If the Mac is asleep at a scheduled time, launchd runs the job once when it
wakes. The install script prints how to trigger a run right away and how to
check its status. A lock file keeps a manual run and the scheduled run
from overlapping.

Notification rules:

- You're notified about open, unacknowledged issues, **each once**. An
  issue is notified again only if it reopens.
- An issue must have been open for `NOTIFY_AFTER_MINUTES` (default 60)
  before it's sent. If it disappears before then (e.g. Intervals.icu links
  the Strava copy of a fresh upload a little later), nothing is sent.
- Everything ready in one run goes out as one message, grouped by
  activity, with links.
- On the first monitor run, every issue that is open and not acknowledged
  counts as new, so you'll get one message with all of them. Acknowledge
  the ones you accept first if you don't want that.
- If syncing with Intervals.icu keeps failing for `HEALTH_ALERT_AFTER_HOURS`
  (default 6) you get one warning, and one message when it works again.

## Notifications

Set `NOTIFIERS` in `.env` to one or more (comma separated) of:

- **pushover**: create an application at https://pushover.net ("Create an
  Application/API Token") and set `PUSHOVER_APP_TOKEN` and
  `PUSHOVER_USER_KEY` (shown on your Pushover dashboard). Optional
  `PUSHOVER_DEVICE` sends to one device only.
- **discord**: a webhook on a channel. For a personal channel, create your
  own server in Discord (Add a Server → Create My Own → For me and my
  friends); only you are in it. Then channel settings → Integrations →
  Webhooks → New Webhook, and put the URL in `DISCORD_WEBHOOK_URL`.
- **console**: just prints the message (useful for testing).

Check the setup with `python main.py notify-test`.

To add another service (Slack, email, ntfy, ...), write a small `Notifier`
subclass in `intervals_auditor/notifiers/` (a `name`, `from_env()` reading its
settings, and `send()`) and register it in `intervals_auditor/notifiers/__init__.py`.

## Logs

- The log file is `data/logs/intervals_auditor.log` (rotated at 1 MB, 5 files kept),
  at `LOG_LEVEL` (default `INFO`). INFO shows what each run did: sync
  counts, removed activities, new/reopened/resolved issues, revoked
  acknowledgments and notifications sent. DEBUG adds per-activity details
  and HTTP calls; TRACE adds full Strava payloads.
- On the console, `run`/`monitor`/`resync` show INFO by default, the other
  commands only warnings. `-v` shows one level more (DEBUG for
  `run`/`monitor`/`resync`, INFO for the others), `-vv` everything
  including TRACE, `-q` only warnings and errors, and `--log-level LEVEL`
  sets both console and log file.
- `python main.py status` shows the last runs and whether syncing is
  currently failing; each run is also stored in the database.
- Under launchd, `data/logs/launchd.log` only gets warnings, errors and
  crashes.

## Syncing

Every run fetches the last `REFRESH_DAYS` (default 30) days. Once the last
full sync is older than `FULL_SYNC_INTERVAL_HOURS` (default 24) it fetches
the whole history instead; `resync` does that on demand.

Activities that Intervals.icu no longer returns for the fetched range have
been deleted there and are removed from the database, and their issues
resolve. If a sync would remove more than half of the activities in the
range at once (and more than 10), it removes nothing and logs a warning, in
case the API returned an incomplete response. If you really did delete that
many, run `python main.py resync --force`.

A failed sync (network, Intervals.icu down) doesn't stop the run: the checks
run on the stored data and the failure is logged.

## Strava enrichment

Some activities are Strava-only "stub" records: Intervals.icu has a
reference to them but can't return full data for them via its API
(`"source": "STRAVA"`, no name, type, or duration -- just a timestamp). This
commonly happens when the Strava-side activity has been cropped, split, or
merged independently of the Garmin-recorded copy, which breaks the
`strava_id` link Intervals.icu stores on the original activity.

When Strava credentials are set in `.env`, every sync fetches the full data
for new stubs directly from the Strava API, so the checks work with the real
thing instead of a single timestamp. Each stub is looked up once: a
successful fetch stores the data, and a confirmed 404 is recorded too, so
it's never queried again. If Strava rate limits us, the run stops fetching
there and the rest is picked up next time.

One-time setup:

1. Create a free app at https://www.strava.com/settings/api (Authorization
   Callback Domain can just be `localhost`).
2. Put the app's Client ID/Secret into `.env` as `STRAVA_CLIENT_ID` /
   `STRAVA_CLIENT_SECRET`.
3. Run `python strava_auth_setup.py` and follow the prompts. It prints a
   `STRAVA_REFRESH_TOKEN` to add to `.env`.

Strava replaces the refresh token now and then, and the old one stops
working immediately. The newest one is stored in the database, so `.env`
doesn't need updating. If you run `strava_auth_setup.py` again and put a
new token in `.env`, that one takes over.

A resolved Strava record is never overwritten by Intervals.icu's
placeholder on later syncs, but if Intervals.icu gets real data of its own
for that activity (e.g. it's linked to a Garmin file), that update is used.

`python main.py strava-export backup.json` / `strava-import backup.json`
back up and restore the Strava-fetched data.

## Data

Everything lives in `data/` (set `DATA_DIR` to move it):

- `intervals_auditor.sqlite`: activities (including Strava data), issues and their
  history, acknowledgments, and a log of runs. It's the application's own
  database, not a cache: don't delete it to refresh data, syncing picks up
  changes and deletions. Deleting it loses your acknowledgments and the
  Strava data.
- `logs/`: log files.
- `intervals_auditor.lock`: prevents overlapping runs.

## Checks

Configuration is in `checks.toml`. Personal, account-specific overrides
(e.g. excluding one of your own activities from a check) go in an untracked
`checks.local.toml` instead -- copy `checks.local.toml.example` to get
started. Each section there is merged on top of the matching section in
`checks.toml`, one level deep, so it never needs to touch the shared,
tracked config.

A normal run uses the enabled checks. Disabling a check resolves its open
issues on the next run. `--only CHECK` runs specific checks, enabled or not,
and leaves other checks' issues untouched.

### duplicate_overlap

Enabled by default. It compares overlapping activities and reports the exact
reasons, including:

- overlap seconds
- overlap percentage of the shorter activity
- activity type match
- start-time difference
- duration difference

It also flags a zero-duration activity (a Strava-only stub -- see "Strava
enrichment" above -- that hasn't been resolved to real data) whose single
timestamp falls inside another activity's time window
(`point_in_time_within_activity`), even though there's no duration to
compute a percentage from.

Each overlapping pair gives one issue on each activity. `ignore_activities`
(in `checks.local.toml`) skips any pair involving those activities.

### under_time_limit

Enabled. Flags activities shorter than `maximum_duration_seconds` (see
`checks.toml`).

### over_time_limit

Enabled. Flags activities longer than `maximum_duration_seconds` (24 hours
by default), likely a tracker that was never stopped.

### garmin_link_only

Enabled, but still under testing and development. API metadata can differ,
so inspect stored `raw_json` and adapt `_is_garmin()` / `_strava_id_none()`
in `intervals_auditor/checks/garmin_link_only.py` to match your data if the heuristic
misfires.

### implausible_pace

Enabled. Flags an activity whose average speed (`distance / moving_time`)
falls outside a plausible range for its activity type -- usually a GPS
glitch, a wrong activity type, or bad distance/duration data. Built-in
per-type bounds live in `intervals_auditor/checks/implausible_pace.py`
(`DEFAULT_SPEED_BOUNDS_KMH`); override or add a type via
`[implausible_pace.speed_kmh_by_type]` in `checks.toml`. Types with no
meaningful pace (`WeightTraining`, `Workout`, `Transition`, `Elliptical`,
...) are skipped automatically.

Each bound is widened by `margin_kmh` (2.0 km/h by default) before anything
is flagged, so a reading only a km/h or two past the line -- ordinary
variation, not bad data -- is left alone. Lower `margin_kmh` in
`checks.toml` for tighter sensitivity, or raise it if even the widened
default still feels too eager.

### missing_distance

Enabled. Flags an outdoor-type activity (`Ride`, `Run`, `Hike`, ... -- see
`outdoor_types` in `checks.toml`) with no recorded GPS distance, once it's
run long enough (`minimum_moving_seconds`) that a missing distance is
unlikely to be coincidental. Indoor trainer/treadmill sessions are always
excluded regardless of type (`Activity.is_trainer` / `.is_indoor`) -- an
indoor "Ride" legitimately has no GPS track, that's not a data problem.

### moving_elapsed_gap

Disabled by default -- the "large elapsed/moving gap means unpaused stopped
time" signal turned out to be mostly noise in practice (long hike/ski breaks
and similar produce it legitimately), not a reliable indicator of bad data.
It also covers a second, more useful case: a *negative* gap (moving time
exceeding elapsed time, which shouldn't be possible) is always flagged at
`medium` severity once it exceeds `negative_gap_tolerance_seconds`. The
check is left in place for that case and for occasional manual use:

```bash
python main.py --offline --only moving_elapsed_gap
```

### Add a new check

1. Create a class in `intervals_auditor/checks/` inheriting `ActivityCheck`.
2. Implement `run(activities)`, returning `self.issue(activity, ...)` for
   each problem (pass `related=[...]` when it involves other activities).
3. Register it in `CHECKS` in `intervals_auditor/checks/__init__.py`.
4. Add its configuration (at least `enabled = true`) to `checks.toml`.

New checks automatically appear in the CLI, reports and notifications.

## Code layout

The code is layered so another interface (e.g. a web UI) can reuse
everything below the CLI:

- `intervals_auditor/cli/`: argument parsing and text output only.
- `intervals_auditor/app.py`: wires settings, database, services and clients; any
  interface creates an `App` and calls its services.
- `intervals_auditor/services/`: sync, Strava enrichment, checks/issue tracking
  (`audit`), acknowledgments, queries for views, runner and monitor. They
  return result objects and log; they never print.
- `intervals_auditor/models.py`: the domain types (`Activity`, `Issue`,
  `IssueRecord`, `Acknowledgment`).
- `intervals_auditor/views.py`: activity-centric read models (`ActivityView`,
  `IssueView`) that the CLI, reports and notifications render.
- `intervals_auditor/repositories.py`, `intervals_auditor/db.py`: SQLite
  access and schema migrations (add a function to `MIGRATIONS` in `db.py`
  to change the schema).
- `intervals_auditor/config.py`: settings from `.env` and `checks.toml`.
- `intervals_auditor/checks/`, `intervals_auditor/notifiers/`,
  `intervals_auditor/sources/` (API clients), and
  `intervals_auditor/reporting/` (the browser report's page is
  `report_template.html`; the data is embedded when it's written).

Run the tests with `python -m unittest`.
