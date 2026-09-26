"""Derived activity statistics that require a judgment call about which
underlying field to trust, given known quirks in how the upstream source
(Garmin Connect, Strava, a raw GPX import, ...) computed its data.

Kept separate from models.py on purpose: Activity there stays a thin,
honest reflection of exactly what's in the cache. A function here means
"we know this field sometimes lies, and here's the specific, tested reason
why" -- add the next heuristic here instead of growing the model into a
pile of special cases that all look like plain field access but aren't.
"""


def average_speed_kmh(activity) -> float | None:
    """Average speed in km/h for an Activity. Normally distance (meters) /
    moving_time (seconds) -- the source already pause-filters moving_time in
    the ordinary case, so this is the right denominator.

    One specific situation makes moving_time unreliable though: some
    activities (seen so far only ones synced from a raw GPX import, e.g.
    device_name "StravaGPX") never got a real pause-filtered moving_time
    computed at all -- the source just set it equal to elapsed_time,
    silently swallowing whatever gap was in the original recording (e.g. a
    multi-hour stop with the GPS timer left running). That produces a
    wildly-too-slow average speed with no way to tell it apart from a
    normal, genuinely continuous activity just by looking at
    moving_time/elapsed_time alone.

    icu_recording_time -- the source's own "how much of this window has
    actual data" figure -- catches it: when moving_time equals elapsed_time
    (no real pause-filtering happened) but icu_recording_time is far smaller
    than that, there was a real, unaccounted-for gap. In that specific case,
    trust the source's own pre-computed average_speed field (derived from
    the real underlying stream) instead of dividing by the misleading
    moving_time.

    This is deliberately narrow rather than always preferring the raw
    field: tested against the full cache, always trusting it introduces new
    bad readings elsewhere (e.g. open-water swim GPS data is its own
    separate mess, and an activity with no distance recorded at all gets a
    nonsense speed from an almost-empty raw record) -- so it's only used in
    the one situation it's actually known to fix. Confirmed against a real
    example (a "Baltur" NordicSki activity): 0.33 km/h via moving_time vs.
    3.66 km/h via this fallback, the latter being the plausible one.
    """
    if not activity.distance:
        return None

    if activity.moving_time and activity.moving_time == activity.elapsed_time:
        recording_time = activity.raw.get("icu_recording_time")
        if (
            recording_time
            and activity.elapsed_time
            and recording_time < activity.elapsed_time * 0.5
        ):
            raw_speed = activity.raw.get("average_speed")
            if raw_speed:
                return raw_speed * 3.6

    if not activity.moving_time:
        return None
    return (activity.distance / 1000) / (activity.moving_time / 3600)
