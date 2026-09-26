"""Activity streams: the time series recorded during an activity (time,
distance, altitude), fetched from Intervals.icu for the checks that need
more than the activity summary -- e.g. how fast you went uphill.

Streams are thinned to a point every few seconds and stored compressed
(a couple of kB per hour), so checks can be re-run with other settings
without fetching anything again.
"""
import json
import zlib
from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterator, NamedTuple

# What's fetched and stored.
STREAM_TYPES = ("time", "distance", "altitude")
# The elevation Intervals.icu corrected from terrain data ("Fixed Alt" in its
# charts) when elevation correction is turned on for the activity -- used
# instead of the device's altitude then.
CORRECTED_ALTITUDE = "fixed_altitude"
# Keep a point at most every this many seconds (the checks look at windows
# of a minute or so, and every second would make the database ~5x bigger).
MIN_STEP_SECONDS = 5


class UnknownStreamsFormat(ValueError):
    pass


@dataclass
class Streams:
    time: list[float]              # seconds since the start (pauses leave gaps)
    distance: list[float]          # meters, cumulative
    altitude: list[float] | None   # meters, None when the activity has no altitude

    def __len__(self) -> int:
        return len(self.time)


def uses_corrected_altitude(activity) -> bool:
    return bool(activity.raw.get("use_elevation_correction"))


def stream_types_for(activity) -> tuple[str, ...]:
    """The streams to fetch for an activity."""
    return STREAM_TYPES + ((CORRECTED_ALTITUDE,) if uses_corrected_altitude(activity) else ())


def version_of(activity) -> str:
    """Identifies the recording the stored streams were made from. It changes
    when the activity changes (fingerprint), Intervals.icu re-analyses it
    (e.g. after trimming it) or elevation correction is turned on or off,
    which calls for fetching the streams again."""
    version = f"{activity.fingerprint or ''}|{activity.raw.get('analyzed') or ''}"
    return version + ("|corrected-altitude" if uses_corrected_altitude(activity) else "")


def parse_api(payload) -> Streams | None:
    """Streams from Intervals.icu's /activity/{id}/streams response: a list of
    {"type": ..., "data": [...]} (a {type: data} mapping is accepted too).
    None if there's no usable time and distance data; UnknownStreamsFormat
    if the response isn't shaped like that at all (so it's retried rather
    than remembered as "no streams")."""
    if isinstance(payload, dict):
        by_type = {k: v for k, v in payload.items() if isinstance(v, list)}
        if not by_type:
            raise UnknownStreamsFormat(f"unexpected streams response with keys {sorted(payload)[:10]}")
    elif isinstance(payload, list):
        if not all(isinstance(s, dict) and "type" in s for s in payload):
            raise UnknownStreamsFormat("unexpected streams response: a list, but not of {type, data} objects")
        by_type = {s["type"]: s.get("data") for s in payload}
    else:
        raise UnknownStreamsFormat(f"unexpected streams response of type {type(payload).__name__}")
    time, distance = by_type.get("time"), by_type.get("distance")
    altitude = by_type.get(CORRECTED_ALTITUDE)
    if not altitude or all(v is None for v in altitude):
        altitude = by_type.get("altitude")
    if not time or not distance:
        return None
    n = min(len(time), len(distance))
    has_altitude = bool(altitude) and any(v is not None for v in altitude)
    t_out, d_out, a_out = [], [], []
    last_altitude = None
    for i in range(n):
        t, d = time[i], distance[i]
        if t is None or d is None:
            continue
        if has_altitude:
            a = altitude[i] if i < len(altitude) else None
            last_altitude = a if a is not None else last_altitude
            if last_altitude is None:
                continue  # no altitude yet at the very start
        t_out.append(float(t))
        d_out.append(float(d))
        a_out.append(last_altitude)
    if len(t_out) < 2:
        return None
    return decimate(Streams(t_out, d_out, a_out if has_altitude else None))


def decimate(streams: Streams, min_step: float = MIN_STEP_SECONDS) -> Streams:
    """Keep a point at most every `min_step` seconds (and always the last one)."""
    keep = [0]
    for i in range(1, len(streams.time)):
        if streams.time[i] - streams.time[keep[-1]] >= min_step:
            keep.append(i)
    if keep[-1] != len(streams.time) - 1:
        keep.append(len(streams.time) - 1)
    pick = lambda values: [values[i] for i in keep] if values is not None else None
    return Streams(pick(streams.time), pick(streams.distance), pick(streams.altitude))


def encode(streams: Streams) -> bytes:
    data = {
        "time": [round(t) for t in streams.time],
        "distance": [round(d, 1) for d in streams.distance],
        "altitude": [round(a, 1) for a in streams.altitude] if streams.altitude is not None else None,
    }
    return zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 9)


def decode(blob: bytes) -> Streams:
    data = json.loads(zlib.decompress(blob).decode("utf-8"))
    return Streams(data["time"], data["distance"], data.get("altitude"))


# --- analysis -------------------------------------------------------------

class Window(NamedTuple):
    start: float        # seconds since the start of the activity
    end: float
    speed_kmh: float    # average speed over the window
    grade_percent: float | None  # average grade, None when unknown


def windows(streams: Streams, seconds: float, *, glitch_kmh: float = 150.0,
            grade_min_distance_m: float = 200.0, smooth_seconds: float = 30.0) -> Iterator[Window]:
    """Every stretch of (at least) `seconds` through the activity, with its
    average speed and grade.

    - Steps faster than `glitch_kmh` are GPS jumps, not movement: they count
      at that speed at most, so one bad point can't make a whole minute look
      fast.
    - Altitude is smoothed over `smooth_seconds` first, and the grade is only
      given for stretches of at least `grade_min_distance_m` -- over a short
      distance, a few meters of altitude noise would be a steep "grade".
    """
    t, d = streams.time, streams.distance
    n = len(t)
    if n < 2:
        return
    # Distance with GPS jumps capped.
    clean = [0.0] * n
    cap = glitch_kmh / 3.6
    for k in range(1, n):
        dt = t[k] - t[k - 1]
        dd = max(0.0, d[k] - d[k - 1])
        if dt <= 0:
            dd = 0.0
        elif dd / dt > cap:
            dd = cap * dt
        clean[k] = clean[k - 1] + dd
    altitude = _smooth(t, streams.altitude, smooth_seconds) if streams.altitude is not None else None

    j = 1
    min_distance = grade_min_distance_m
    for i in range(n - 1):
        ti = t[i]
        if j <= i:
            j = i + 1
        while j < n and t[j] - ti < seconds:
            j += 1
        if j >= n:
            return
        distance = clean[j] - clean[i]
        grade = None
        if altitude is not None and distance >= min_distance:
            grade = (altitude[j] - altitude[i]) / distance * 100
            grade = 40.0 if grade > 40.0 else -40.0 if grade < -40.0 else grade
        yield Window(ti, t[j], distance / (t[j] - ti) * 3.6, grade)


def _smooth(t: list[float], values: list[float], seconds: float) -> list[float]:
    """Centered moving average over `seconds` of time."""
    half = seconds / 2
    out, lo, hi, total = [], 0, 0, 0.0
    n = len(values)
    for i in range(n):
        while hi < n and t[hi] <= t[i] + half:
            total += values[hi]
            hi += 1
        while t[lo] < t[i] - half:
            total -= values[lo]
            lo += 1
        out.append(total / (hi - lo))
    return out


class Interpolator:
    """Piecewise-linear lookup in [[x, y], ...] points; flat beyond the first
    and last point."""

    def __init__(self, points: list[list[float]]):
        points = sorted((float(x), float(y)) for x, y in points)
        self.xs = [x for x, _ in points]
        self.ys = [y for _, y in points]

    def __call__(self, x: float) -> float:
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect_left(xs, x)
        x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def interpolate(points: list[list[float]], x: float) -> float:
    return Interpolator(points)(x)


def clock(seconds: float) -> str:
    """Seconds since the start as h:mm:ss."""
    seconds = int(round(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
