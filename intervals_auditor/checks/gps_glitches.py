"""GPS glitches in a recording: not you going too fast, but bad data that
skews top speeds and records -- and that you may want to crop in
Intervals.icu (or wherever the activity came from).

- A spike: the top speed the device recorded is far above anything the
  recording holds for 10 seconds (e.g. 109.8 km/h recorded on a ride that
  was never faster than 52 km/h over 10 s).
- A spike out and back: the position flies off and comes back within
  seconds, adding distance far beyond the straight line from before to
  after it (needs GPS positions).
- A jump: the position leaps hundreds of meters from one point to the next,
  or catches up in a short burst after the GPS signal comes back (e.g. 260 m
  in 17 s in the middle of a walk).

Both need the activity's streams, fetched from Intervals.icu for the types
in activity_types. The issue says where each glitch is (h:mm:ss from the
start), which is what you need to crop it. Indoor/trainer sessions are
skipped.
"""
from intervals_auditor.streams import clock, find_jumps, top_speed

from .base import ActivityCheck

DEFAULT_ACTIVITY_TYPES = ["Ride", "GravelRide", "MountainBikeRide", "EBikeRide", "Run", "TrailRun", "Walk", "Hike"]
DEFAULT_SPIKE_MARGIN_KMH = 15
HELD_SECONDS = 10


class GpsGlitchesCheck(ActivityCheck):
    name = "gps_glitches"
    description = (
        "Finds GPS glitches in the recording: a recorded top speed far above anything held for "
        "10 seconds (a one-sample spike), or the position jumping hundreds of meters at once. "
        "Bad data rather than real speed; crop it if you want it gone."
    )

    @classmethod
    def stream_activity_types(cls, config: dict) -> set[str]:
        return set(config.get("activity_types", DEFAULT_ACTIVITY_TYPES))

    @classmethod
    def validate_config(cls, config: dict) -> None:
        types = config.get("activity_types", DEFAULT_ACTIVITY_TYPES)
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise ValueError("activity_types must be a list of activity types")
        margin = config.get("spike_margin_kmh", DEFAULT_SPIKE_MARGIN_KMH)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or margin <= 0:
            raise ValueError("spike_margin_kmh must be a number above 0")

    def run(self, activities):
        types = self.stream_activity_types(self.config)
        margin = float(self.config.get("spike_margin_kmh", DEFAULT_SPIKE_MARGIN_KMH))
        issues = []
        for a in activities:
            if a.activity_type not in types or a.is_trainer or a.is_indoor:
                continue
            streams = self.streams(a)
            if streams is None:
                continue
            summaries, details = [], []

            recorded = float(a.raw.get("max_speed") or 0) * 3.6
            held = top_speed(streams, HELD_SECONDS)
            if recorded and held is not None and recorded - held >= margin:
                summaries.append(f"GPS spike: recorded top speed {recorded:.1f} km/h, but never faster than "
                                 f"{held:.0f} km/h over {HELD_SECONDS} s")
                details.append({"rule": "speed_spike", "recorded_max_speed_kmh": round(recorded, 1),
                                "top_speed_over_10s_kmh": round(held, 1)})

            glitches = find_jumps(streams)
            spikes = [g for g in glitches if g.kind == "spike"]
            if spikes:
                extra = lambda g: g.meters - (g.straight or 0.0)
                biggest = max(spikes, key=extra)
                if len(spikes) == 1:
                    summaries.append(f"GPS spike out and back (+{extra(biggest):.0f} m) at {clock(biggest.at)}")
                else:
                    first = "" if spikes[0] is biggest else f" (the first at {clock(spikes[0].at)})"
                    summaries.append(f"{len(spikes)} GPS spikes out and back, the biggest +{extra(biggest):.0f} m "
                                     f"at {clock(biggest.at)}{first}")
                details.append({"rule": "position_spike", "count": len(spikes),
                                "spikes": [{"at": clock(g.at), "extra_meters": round(extra(g)),
                                            "seconds": round(g.seconds)} for g in spikes[:20]]})
            jumps = [g for g in glitches if g.kind == "jump"]
            if jumps:
                biggest = max(jumps, key=lambda g: g.meters)
                size = f"{biggest.meters:.0f} m" + (f" in {biggest.seconds:.0f} s" if biggest.seconds >= 10 else "")
                if len(jumps) == 1:
                    summaries.append(f"GPS jump of {size} at {clock(biggest.at)}")
                else:
                    first = "" if jumps[0] is biggest else f" (the first at {clock(jumps[0].at)})"
                    summaries.append(f"{len(jumps)} GPS jumps, the biggest {size} at {clock(biggest.at)}{first}")
                details.append({"rule": "position_jump", "count": len(jumps),
                                "jumps": [{"at": clock(g.at), "meters": round(g.meters), "seconds": round(g.seconds)}
                                          for g in jumps[:20]]})

            if summaries:
                issues.append(self.issue(a, "; ".join(summaries), details, "info"))
        return issues
