"""Activity fingerprints: a short hash of the activity fields the checks care
about. Used to notice that an activity has changed since it was last seen --
in particular since an issue on it was acknowledged, which reopens that
issue.

Only fields that matter for data quality are included. Renaming an
activity, editing its description, or Intervals.icu re-running its own
analysis (training load, zones, ...) should not reopen an acknowledgment;
changing its timing, distance, type, source or links should.
"""
import hashlib
import json

FINGERPRINT_FIELDS = (
    "type",
    "start_date",
    "start_date_local",
    "elapsed_time",
    "moving_time",
    "distance",
    "source",
    "strava_id",
    "external_id",
    "trainer",
    "indoor",
    "icu_recording_time",
    # Set by Strava enrichment -- a stub getting resolved is a real change.
    "_strava_lookup",
)

# Distances are floats that can wobble slightly when Intervals.icu
# reprocesses a file; anything below this many meters is not a real change.
DISTANCE_ROUNDING_METERS = 10


def activity_fingerprint(payload: dict) -> str:
    values = {}
    for field in FINGERPRINT_FIELDS:
        value = payload.get(field)
        if field == "type":
            value = value or payload.get("activity_type")
        if field == "distance" and value is not None:
            try:
                value = int(round(float(value) / DISTANCE_ROUNDING_METERS)) * DISTANCE_ROUNDING_METERS
            except (TypeError, ValueError):
                pass
        values[field] = value
    encoded = json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
