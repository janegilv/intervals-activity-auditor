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
new activity has a problem. There's also a web interface, which can run
on another machine in Docker.

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
python main.py fetch-streams         # fetch all activity streams the checks need now
python main.py reset-issues --yes    # start over: delete all issues and acknowledgments, number from 1
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
  and one `unack` for the acknowledged ones. "Select all" above the list
  ticks everything the current filters show. Each issue also has its own
  copy button. `ack` and `unack` update the report files, so after
  running the command just reload the page. The command prefix
  (`python main.py`, `./main.py`, ...) can be changed at the bottom of
  the page. Filters and settings are kept while the tab is open (also
  across reloads) and start fresh in a new tab.
- `report.md`, `report.json` and `report.csv` (one row per activity
  issue) for reading, scripts and spreadsheets.

## How issues and acknowledgments work

Each issue has a severity: `high`, `medium`, `low` or `info`. `info` is
used only for GPS glitches (`gps_glitches`), so filtering out `info` on the
Issues page (or in `report.html`) hides them and nothing else.

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

## Web interface

```bash
pip install -r requirements-web.txt
python main.py serve              # http://127.0.0.1:8080/
```

The web interface shows the same activity-centric issue list as
`report.html` (most severe first; pick another order in the sort menu),
but acknowledging and unacknowledging happen right there
(one issue, or tick several / "Select all" and do them in one go). Each
activity has its own page with its issues, their history and its
acknowledgments. There are pages for all acknowledgments (including
revoked ones), for status (sync health, recent runs, the log, and buttons
for sync, full resync and re-running the checks), and for the check
settings. The JSON API behind it is documented at `/api/docs`.

While it runs, the server also runs the monitor every
`MONITOR_INTERVAL_MINUTES` (default 30; first run a minute after start), so
it replaces the launchd job: run `scripts/uninstall-launchd.sh` first, or
you'll get notifications twice. To keep launchd instead, start it with
`--no-monitor` (or set `MONITOR_INTERVAL_MINUTES=0`). The CLI keeps working
next to it; they share the database, and the lock file keeps runs from
overlapping.

Check settings you change in the web interface are stored in the database
and win over `checks.toml`/`checks.local.toml`; saving re-runs the checks.
`python main.py checks` shows what was changed, and "Reset to file" in the
web interface removes the changes again.

**There is no login yet.** By default the server only listens on this
machine (`WEB_HOST=127.0.0.1`). `--host 0.0.0.0` (or `WEB_HOST`) lets other
machines in; only do that on a network you trust. Authentication will go in
`intervals_auditor/web/security.py`, which every page and API call already
goes through.

## Running on a server with Docker

`Dockerfile` and `compose.yaml` run the web interface and the monitor in
one container. Everything that changes lives in `./data` on the server
(mounted at `/data`): the database, logs, reports and your
`checks.local.toml`. Settings and credentials come from `.env`.

1. Get the project onto the server (`git clone`, then check out this
   branch), and copy your `.env` next to `compose.yaml`.
2. Move the database from the Mac so the history and your
   acknowledgments come along. Stop everything that uses it first
   (`scripts/uninstall-launchd.sh`, and any running `serve`), then copy
   `data/intervals_auditor.sqlite` into `data/` on the server; if
   `intervals_auditor.sqlite-wal`/`-shm` files are still there, copy them
   too. Put `checks.local.toml` in `data/` as well. The Strava refresh
   token is stored in the database, so it moves with it.
3. Check `compose.yaml`: `TZ` (your time zone), and `AUDITOR_UID`/`AUDITOR_GID`
   if `data/` isn't owned by user 1000 on the server (set them in `.env`,
   e.g. `AUDITOR_UID=1026`).
4. Start it and open `http://<server>:8080/`:

   ```bash
   docker compose up -d --build
   docker compose logs -f                              # console output
   docker compose exec auditor python main.py status   # the CLI works too
   ```

5. To update: `git pull && docker compose up -d --build`.

Only let one place run the monitor (the Mac *or* the server), otherwise
you get every notification twice and two databases drift apart. The port is
published on all of the server's addresses; there is no login yet, so keep
it on a network you trust, or change the port mapping to
`127.0.0.1:8080:8080` and reach it through an SSH tunnel or similar.

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

The database also holds the activity streams the checks need (see
`implausible_pace`), thinned to a point every 5 seconds and compressed:
roughly 15 kB per activity.

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

Enabled. Finds speeds that don't fit the activity type -- a GPS glitch, the
wrong activity type, or part of the activity done in a car, bus, lift or
train. Three rules, each with a table per activity type in `checks.toml`
(also shown and editable on the web interface's Checks page). A table is
used as it is: a type that isn't listed isn't checked by that rule.

- **Average speed** (`distance / moving_time`) outside
  `speed_kmh_by_type` = `[lowest, highest]` km/h. Each bound is widened by
  `margin_kmh` (2.0) first, so a reading a km/h or two past the line --
  ordinary variation, not bad data -- is left alone.
- **Top speed** above `max_speed_kmh_by_type` (e.g. 20 km/h for Hike and
  Walk, 100 for Ride): the fastest 10 seconds of the activity's streams (see
  below), so a one-sample GPS spike doesn't count -- `gps_glitches` reports
  those. Without streams, the top speed the device recorded. On foot, far
  above the limit means a ride in a vehicle.
- **Sustained speed for the grade**: the average speed over any
  `sustained_seconds` (60) must not be faster than
  `sustained_speed_kmh_by_grade` allows for the grade there. The table has
  `[grade %, km/h]` points per type, linear in between. Downhill a bike can
  be as fast as a car (100 km/h at -8%), uphill it can't (32 km/h at +6%,
  24 at +10%), so a car, bus or lift ride inside a ride or hike shows up on
  the climbs. The issue says how fast, at what grade and where in the
  activity (h:mm:ss from the start), and for how long it was too fast.
  GPS jumps don't count as distance, and sudden altitude jumps (over 5 m/s,
  the device correcting itself) don't count as climbing. Devices often
  settle their altitude for a few minutes after the start and after a pause
  (one ride drifted 150 m during a 30-minute stop and "climbed" back after
  it), so for `altitude_settle_seconds` (300) after the start and after each
  pause of a minute or more -- and wherever there's no altitude -- only the
  table's highest speed applies.

Top speed and sustained speed skip indoor and trainer sessions (their speed
is virtual). All findings on one activity make one issue.

The sustained rule (and `gps_glitches`) needs each activity's **streams**
(time, distance, altitude and GPS position over the activity), which the
summary Intervals.icu lists doesn't have. They're fetched separately for
the types in `sustained_speed_kmh_by_grade`, one request per activity: new activities
right away, the rest of the history at most `STREAMS_PER_RUN` (100) per
run, newest first -- or all at once with `python main.py fetch-streams`
(then `python main.py run --offline` to check them). An activity is fetched
again only if it changes.

If the device's altitude is wrong, turn on elevation correction for the
activity in Intervals.icu: the corrected elevation ("Fixed Alt" in its
charts) is then used instead. Like any edit to an activity older than
`REFRESH_DAYS`, it's picked up by the next full sync (daily), or right away
with `python main.py resync` (Full resync on the web interface's Status
page); the streams are then fetched again and the activity re-checked. `python main.py status` (and the web interface's
Status page) shows how many are stored and waiting. Activities that came
from Strava have no streams on Intervals.icu, so only their summary is
checked.

### gps_glitches

Enabled, at "info" level. Finds GPS glitches: not you going too fast, but
bad data that skews top speeds and records, and that you may want to crop
in Intervals.icu (or wherever the activity came from). Uses the same
streams as `implausible_pace`, for the types in `activity_types`.

- **Spike**: the top speed the device recorded is at least
  `spike_margin_kmh` (15) above the fastest the recording holds for 10
  seconds, e.g. 109.8 km/h recorded on a ride never faster than 52 km/h
  over 10 s.
- **Spike out and back**: the position flies off and comes back within
  seconds. Over the few points around it, the watch adds far more distance
  (at least 100 m, and twice as much) than the straight line from before
  to after it. This works at any speed, also on a bike, and the detour
  counts as that straight line for `implausible_pace`.
- **Jump**: the position leaps from one point to the next (faster than
  150 km/h), or catches up in a short burst when the GPS signal comes back
  -- at most 30 seconds, at least 5 times (and 25 km/h) faster than the
  two minutes before and after, e.g. 260 m in 17 s in the middle of a
  hike. A bus ride or a real sprint lasts longer or isn't that much faster,
  so it doesn't count as a jump. Jumps don't count as distance for
  `implausible_pace`.

Tunnels (and deep valleys or dense forest) aren't glitches: the position
stands still while the signal is gone, then catches up. When the speed over
the stuck stretch and the jump together is at most twice the speed around
it, that's a signal dropout: not reported, and for `implausible_pace` the
distance is spread over the time the signal was gone. A barometric
altimeter can also go wild in a long tunnel (the air pressure changes); if
that makes a ride look too fast for the grade, turn on elevation correction
for it in Intervals.icu.

The issue says where each glitch is (h:mm:ss from the start). If it's at
the very start, cropping the activity usually fixes it; the activity then
changes, its streams are fetched again and the issue resolves. If you don't
care about glitches, acknowledge the issues or turn the check off.

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

When it's on, a gap is reported as `low`, or `medium` from
`major_gap_seconds` (6 hours) of stopped time: then the recording was
usually saved hours after the activity ended (or started hours before it),
and trimming the start or end in Intervals.icu fixes it.

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

The code is layered so the CLI and the web interface share everything
below them:

- `intervals_auditor/cli/`: argument parsing and text output only.
- `intervals_auditor/web/`: the web interface: a FastAPI JSON API
  (`api.py`), background jobs and the monitor schedule (`jobs.py`), and the
  single-page frontend (`static/`).
- `intervals_auditor/ui/`: the issue browser (JavaScript and CSS) shared by
  `report.html` and the web interface.
- `intervals_auditor/app.py`: wires settings, database, services and clients; any
  interface creates an `App` and calls its services.
- `intervals_auditor/services/`: sync, Strava enrichment, fetching
  activity streams, checks/issue tracking (`audit`), acknowledgments,
  queries for views, runner and monitor. They return result objects and
  log; they never print.
- `intervals_auditor/streams.py`: activity streams -- how they're stored,
  and the per-minute speed/grade windows the checks look at.
- `intervals_auditor/models.py`: the domain types (`Activity`, `Issue`,
  `IssueRecord`, `Acknowledgment`).
- `intervals_auditor/views.py`: activity-centric read models (`ActivityView`,
  `IssueView`) that the CLI, reports and notifications render.
- `intervals_auditor/repositories.py`, `intervals_auditor/db.py`: SQLite
  access and schema migrations (add a function to `MIGRATIONS` in `db.py`
  to change the schema).
- `intervals_auditor/config.py`: settings from `.env` and `checks.toml`
  (changes made in the web interface are applied on top by
  `services/check_settings.py`).
- `intervals_auditor/checks/`, `intervals_auditor/notifiers/`,
  `intervals_auditor/sources/` (API clients), and
  `intervals_auditor/reporting/` (the browser report's page is
  `report_template.html`; the data is embedded when it's written).

Run the tests with `python -m unittest`.
