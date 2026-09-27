"""Activity streams: the time series recorded during an activity (time,
distance, altitude and GPS position), fetched from Intervals.icu for the
checks that need more than the activity summary -- e.g. how fast you went
uphill, or whether the GPS position jumped.

Streams are thinned to a point every few seconds and stored compressed
(a couple of kB per hour), so checks can be re-run with other settings
without fetching anything again.
"""
import json
import math
import zlib
from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterator, NamedTuple

# What's fetched and stored (plus "latlng" when the activity has GPS positions).
STREAM_TYPES = ("time", "distance", "altitude")
POSITIONS = "latlng"
# Part of every stored version: changing what's stored makes everything be
# fetched again.
STORED_FORMAT = "positions"
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
    lat: list[float] | None = None  # GPS position, None when there's none
    lng: list[float] | None = None

    def __len__(self) -> int:
        return len(self.time)


def uses_corrected_altitude(activity) -> bool:
    return bool(activity.raw.get("use_elevation_correction"))


def stream_types_for(activity) -> tuple[str, ...]:
    """The streams to fetch for an activity."""
    types = STREAM_TYPES
    if POSITIONS in (activity.raw.get("stream_types") or []):
        types += (POSITIONS,)
    if uses_corrected_altitude(activity):
        types += (CORRECTED_ALTITUDE,)
    return types


def version_of(activity) -> str:
    """Identifies the recording the stored streams were made from. It changes
    when the activity changes (fingerprint), Intervals.icu re-analyses it
    (e.g. after trimming it) or elevation correction is turned on or off,
    which calls for fetching the streams again."""
    version = f"{activity.fingerprint or ''}|{activity.raw.get('analyzed') or ''}|{STORED_FORMAT}"
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
        by_type = {s["type"]: (s if s["type"] == POSITIONS else s.get("data")) for s in payload}
    else:
        raise UnknownStreamsFormat(f"unexpected streams response of type {type(payload).__name__}")
    time, distance = by_type.get("time"), by_type.get("distance")
    altitude = by_type.get(CORRECTED_ALTITUDE)
    if not altitude or all(v is None for v in altitude):
        altitude = by_type.get("altitude")
    if not time or not distance:
        return None
    positions = _positions(by_type.get(POSITIONS))
    n = min(len(time), len(distance))
    has_altitude = bool(altitude) and any(v is not None for v in altitude)
    t_out, d_out, a_out, p_out = [], [], [], []
    last_altitude = last_position = None
    for i in range(n):
        t, d = time[i], distance[i]
        if t is None or d is None:
            continue
        if has_altitude:
            a = altitude[i] if i < len(altitude) else None
            last_altitude = a if a is not None else last_altitude
            if last_altitude is None:
                continue  # no altitude yet at the very start
        if positions is not None:
            position = positions[i] if i < len(positions) else None
            last_position = position or last_position
        t_out.append(float(t))
        d_out.append(float(d))
        a_out.append(last_altitude)
        p_out.append(last_position)
    if len(t_out) < 2:
        return None
    lat = lng = None
    first_fix = next((p for p in p_out if p is not None), None)
    if first_fix is not None:  # before the first fix, use the first fix
        p_out = [p or first_fix for p in p_out]
        lat, lng = [p[0] for p in p_out], [p[1] for p in p_out]
    return decimate(Streams(t_out, d_out, a_out if has_altitude else None, lat, lng))


def _positions(stream) -> list[tuple[float, float] | None] | None:
    """GPS positions from the latlng stream: Intervals.icu gives latitudes in
    "data" and longitudes in "data2"; [lat, lng] pairs are accepted too.
    None if it's missing or not understood (positions are optional)."""
    if isinstance(stream, dict):
        lat, lng = stream.get("data"), stream.get("data2")
        if isinstance(lat, list) and isinstance(lng, list):
            pairs = list(zip(lat, lng))
        else:
            pairs = lat
    else:
        pairs = stream
    if not isinstance(pairs, list) or not pairs:
        return None
    out = []
    for p in pairs:
        if (isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p)
                and (p[0], p[1]) != (0, 0)):
            out.append((float(p[0]), float(p[1])))
        elif p is None or isinstance(p, (list, tuple)):
            out.append(None)
        else:
            return None
    return out if any(out) else None


def decimate(streams: Streams, min_step: float = MIN_STEP_SECONDS) -> Streams:
    """Keep a point at most every `min_step` seconds (and always the last one)."""
    keep = [0]
    for i in range(1, len(streams.time)):
        if streams.time[i] - streams.time[keep[-1]] >= min_step:
            keep.append(i)
    if keep[-1] != len(streams.time) - 1:
        keep.append(len(streams.time) - 1)
    pick = lambda values: [values[i] for i in keep] if values is not None else None
    return Streams(pick(streams.time), pick(streams.distance), pick(streams.altitude),
                   pick(streams.lat), pick(streams.lng))


def encode(streams: Streams) -> bytes:
    data = {
        "time": [round(t) for t in streams.time],
        "distance": [round(d, 1) for d in streams.distance],
        "altitude": [round(a, 1) for a in streams.altitude] if streams.altitude is not None else None,
        "lat": [round(v, 5) for v in streams.lat] if streams.lat is not None else None,
        "lng": [round(v, 5) for v in streams.lng] if streams.lng is not None else None,
    }
    return zlib.compress(json.dumps(data, separators=(",", ":")).encode("utf-8"), 9)


def decode(blob: bytes) -> Streams:
    data = json.loads(zlib.decompress(blob).decode("utf-8"))
    return Streams(data["time"], data["distance"], data.get("altitude"), data.get("lat"), data.get("lng"))


# --- analysis -------------------------------------------------------------

class Window(NamedTuple):
    start: float        # seconds since the start of the activity
    end: float
    speed_kmh: float    # average speed over the window
    grade_percent: float | None  # average grade, None when unknown


# A step between two points faster than this is the GPS position jumping,
# not movement.
GPS_JUMP_KMH = 150.0
# Steps at least this many times (and BURST_MIN_EXCESS_KMH) faster than the
# two minutes around them are looked at more closely; a stretch of them
# lasting at most BURST_MAX_SECONDS is a GPS glitch when
# - the GPS positions show it went out and came back (a spike): the watch
#   added at least SPIKE_MIN_EXTRA_M and SPIKE_PATH_RATIO times more
#   distance than the straight line from before to after it; or
# - it's at least BURST_RATIO times faster than around it: the position
#   catching up after the GPS signal comes back (e.g. 260 m in 17 s in the
#   middle of a walk). A bus ride lasts longer, a sprint isn't that much
#   faster.
CANDIDATE_RATIO = 2.0
BURST_MAX_SECONDS = 30.0
BURST_RATIO = 5.0
BURST_MIN_EXCESS_KMH = 25.0
BURST_CONTEXT_SECONDS = 120.0
SPIKE_MIN_EXTRA_M = 100.0
SPIKE_PATH_RATIO = 2.0
# A jump right after the position stood still (slower than STUCK_KMH) is the
# GPS signal coming back after a tunnel (or a deep valley or dense forest):
# the distance is real, it just arrives all at once. It's a dropout, not a
# glitch, when the speed over the stuck stretch and the jump together is at
# most DROPOUT_MAX_SPEED_RATIO times the faster of the two minutes before
# and after (slower is always possible: you may have stopped in there).
STUCK_KMH = 2.0
DROPOUT_MAX_SECONDS = 900.0
DROPOUT_MAX_SPEED_RATIO = 2.0
# Altitude changing faster than this (m/s) is the device correcting its
# altitude (e.g. right after the start, or after losing the GPS signal),
# not climbing or descending.
ALTITUDE_JUMP_M_PER_S = 5.0
# A gap in the recording at least this long is a pause (auto-pause or the
# stop button), after which the altitude may need to settle again.
PAUSE_SECONDS = 60.0


class Jump(NamedTuple):
    first: int      # index of the point before the glitch
    last: int       # index of the point after it
    at: float       # seconds since the start
    meters: float   # distance the watch added
    seconds: float
    kind: str       # "spike" (out and back), "jump" (the position leaps ahead) or
                    # "dropout" (the signal lost, e.g. in a tunnel: first is where it got stuck)
    straight: float | None = None  # straight-line meters from before to after, if positions are known


def meters_between(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2)
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(a)))


def find_jumps(streams: Streams, jump_kmh: float = GPS_JUMP_KMH) -> list[Jump]:
    """Where the GPS position glitches: spikes out and back (needs positions),
    the position catching up in a short burst, and single steps faster than
    `jump_kmh`. See the constants above."""
    cache = streams.__dict__.setdefault("_jumps", {})
    if jump_kmh in cache:
        return cache[jump_kmh]
    t, d = streams.time, streams.distance
    n = len(t)
    positions = streams.lat is not None and streams.lng is not None
    # Speed of the step from point k-1 to k.
    speeds = [0.0] + [max(0.0, d[k] - d[k - 1]) / (t[k] - t[k - 1]) * 3.6 if t[k] > t[k - 1] else 0.0
                      for k in range(1, n)]
    # Average speed (km/h) in the minutes before and after each point.
    before, after = [0.0] * n, [0.0] * n
    j = 0
    for i in range(n):
        while t[j] < t[i] - BURST_CONTEXT_SECONDS:
            j += 1
        before[i] = (d[i] - d[j]) / (t[i] - t[j]) * 3.6 if t[i] > t[j] else 0.0
    j = 0
    for i in range(n):
        j = max(j, i)
        while j + 1 < n and t[j + 1] <= t[i] + BURST_CONTEXT_SECONDS:
            j += 1
        after[i] = (d[j] - d[i]) / (t[j] - t[i]) * 3.6 if t[j] > t[i] else 0.0

    def around(first: int, last: int) -> float:  # km/h in the minutes before and after
        return max(before[first], after[last])

    def candidate(k: int) -> bool:
        v = speeds[k]
        if v > jump_kmh:
            return True
        return v >= BURST_MIN_EXCESS_KMH and v >= CANDIDATE_RATIO * around(k - 1, k)

    jumps, k = [], 1
    while k < n:
        if speeds[k] < BURST_MIN_EXCESS_KMH or not candidate(k):
            k += 1
            continue
        end = k  # a stretch of suspicious steps: k .. end
        while end + 1 < n and speeds[end + 1] >= BURST_MIN_EXCESS_KMH and candidate(end + 1):
            end += 1
        first, last = k - 1, end
        seconds, meters = t[last] - t[first], d[last] - d[first]
        straight = (meters_between(streams.lat[first], streams.lng[first], streams.lat[last], streams.lng[last])
                    if positions else None)
        base = around(first, last)
        speed = meters / seconds * 3.6 if seconds > 0 else 0.0
        if seconds <= BURST_MAX_SECONDS and straight is not None and \
                meters - straight >= SPIKE_MIN_EXTRA_M and meters >= SPIKE_PATH_RATIO * straight:
            jumps.append(Jump(first, last, t[first], meters, seconds, "spike", straight))
        elif seconds <= BURST_MAX_SECONDS and speed >= BURST_RATIO * base and speed - base >= BURST_MIN_EXCESS_KMH:
            jumps.append(Jump(first, last, t[first], meters, seconds, "jump", straight))
        else:
            for i in range(k, end + 1):
                if speeds[i] > jump_kmh:
                    step = (meters_between(streams.lat[i - 1], streams.lng[i - 1], streams.lat[i], streams.lng[i])
                            if positions else None)
                    out_and_back = step is not None and d[i] - d[i - 1] >= SPIKE_PATH_RATIO * step
                    jumps.append(Jump(i - 1, i, t[i - 1], d[i] - d[i - 1], t[i] - t[i - 1],
                                      "spike" if out_and_back else "jump", step))
        k = end + 1
    jumps = [_as_dropout(jump, t, d, speeds, before, after) or jump if jump.kind == "jump" else jump
             for jump in jumps]
    cache[jump_kmh] = jumps
    return jumps


def _as_dropout(jump: Jump, t, d, speeds, before, after) -> Jump | None:
    """The jump as a signal dropout (see STUCK_KMH), or None if it isn't one."""
    stuck = jump.first
    while stuck > 0 and speeds[stuck] < STUCK_KMH and t[jump.first] - t[stuck - 1] <= DROPOUT_MAX_SECONDS:
        stuck -= 1
    if stuck == jump.first:
        return None
    seconds = t[jump.last] - t[stuck]
    meters = d[jump.last] - d[stuck]
    speed = meters / seconds * 3.6 if seconds > 0 else 0.0
    if speed > DROPOUT_MAX_SPEED_RATIO * max(before[stuck], after[jump.last]):
        return None
    return Jump(stuck, jump.last, t[stuck], meters, seconds, "dropout", jump.straight)


def gps_jumps(streams: Streams, jump_kmh: float = GPS_JUMP_KMH) -> list[tuple[float, float, float]]:
    """(seconds since the start, meters, over how many seconds) per GPS glitch
    (dropouts, e.g. tunnels, aren't glitches)."""
    return [(j.at, j.meters, j.seconds) for j in find_jumps(streams, jump_kmh) if j.kind != "dropout"]


def _distance_without_jumps(streams: Streams, jump_kmh: float) -> list[float]:
    """Cumulative distance without GPS glitches: a spike out and back counts
    as the straight line from before to after it; a dropout's distance is
    spread over the time the signal was lost; a jump counts as no distance
    (the real movement during it is unknown; leaving it out can't make
    anything look too fast). Kept on the streams object, as several rules
    use it."""
    cache = streams.__dict__.setdefault("_without_jumps", {})
    if jump_kmh in cache:
        return cache[jump_kmh]
    t, d = streams.time, streams.distance
    # Per step inside a glitch: meters per second to count instead (a spike's
    # straight line spread over its duration; 0 for a jump).
    in_glitch = {}
    for jump in find_jumps(streams, jump_kmh):
        rate = 0.0
        if jump.kind == "spike" and jump.straight is not None and jump.seconds > 0:
            rate = jump.straight / jump.seconds
        elif jump.kind == "dropout" and jump.seconds > 0:  # the distance, spread over the time it took
            rate = jump.meters / jump.seconds
        in_glitch.update((k, rate) for k in range(jump.first + 1, jump.last + 1))
    clean = [0.0] * len(t)
    for k in range(1, len(t)):
        dd = max(0.0, d[k] - d[k - 1])
        if t[k] <= t[k - 1]:
            dd = 0.0
        elif k in in_glitch:
            dd = in_glitch[k] * (t[k] - t[k - 1])
        clean[k] = clean[k - 1] + dd
    cache[jump_kmh] = clean
    return clean


def _altitude_without_jumps(t: list[float], altitude: list[float], max_rate: float) -> list[float]:
    out = [altitude[0]]
    for k in range(1, len(altitude)):
        dt, da = t[k] - t[k - 1], altitude[k] - altitude[k - 1]
        if dt <= 0 or abs(da) / dt > max_rate:
            da = 0.0
        out.append(out[-1] + da)
    return out


def top_speed(streams: Streams, seconds: float = 10.0, jump_kmh: float = GPS_JUMP_KMH) -> float | None:
    """The fastest average speed (km/h) held over `seconds`, GPS jumps left out."""
    t = streams.time
    clean = _distance_without_jumps(streams, jump_kmh)
    best, j = None, 1
    for i in range(len(t) - 1):
        if j <= i:
            j = i + 1
        while j < len(t) and t[j] - t[i] < seconds:
            j += 1
        if j >= len(t):
            break
        speed = (clean[j] - clean[i]) / (t[j] - t[i]) * 3.6
        best = speed if best is None or speed > best else best
    return best


def windows(streams: Streams, seconds: float, *, jump_kmh: float = GPS_JUMP_KMH,
            altitude_jump_rate: float = ALTITUDE_JUMP_M_PER_S, settle_seconds: float = 0.0,
            pause_seconds: float = PAUSE_SECONDS,
            grade_min_distance_m: float = 200.0, smooth_seconds: float = 30.0) -> Iterator[Window]:
    """Every stretch of (at least) `seconds` through the activity, with its
    average speed and grade.

    - GPS jumps (steps faster than `jump_kmh`) count as no distance, so a bad
      point can't make a whole minute look fast.
    - Altitude jumps (faster than `altitude_jump_rate` m/s) don't count as
      climbing. Devices often settle their altitude for a while after the
      start and after a pause (e.g. drifting 150 m during a 30-minute stop
      and climbing back slowly afterwards), so stretches starting within
      `settle_seconds` of the start or of resuming after a pause of at least
      `pause_seconds`, or spanning such a pause, get no grade.
    - Altitude is smoothed over `smooth_seconds`, and the grade is only given
      for stretches of at least `grade_min_distance_m` -- over a short
      distance, a few meters of altitude noise would be a steep "grade".
    """
    t = streams.time
    n = len(t)
    if n < 2:
        return
    clean = _distance_without_jumps(streams, jump_kmh)
    altitude = None
    if streams.altitude is not None:
        altitude = _smooth(t, _altitude_without_jumps(t, streams.altitude, altitude_jump_rate), smooth_seconds)

    # Where the recording (re)starts: the start, and the first point after each pause.
    starts = [0] + [k for k in range(1, n) if t[k] - t[k - 1] >= pause_seconds]
    j, s_index = 1, 0
    min_distance = grade_min_distance_m
    for i in range(n - 1):
        ti = t[i]
        if j <= i:
            j = i + 1
        while j < n and t[j] - ti < seconds:
            j += 1
        if j >= n:
            return
        while s_index + 1 < len(starts) and starts[s_index + 1] <= i:
            s_index += 1
        next_start = starts[s_index + 1] if s_index + 1 < len(starts) else n
        settled = ti - t[starts[s_index]] >= settle_seconds and next_start > j
        distance = clean[j] - clean[i]
        grade = None
        if altitude is not None and distance >= min_distance and settled:
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
