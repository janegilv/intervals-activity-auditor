from .base import ActivityCheck

# Below this many seconds the activity is most likely a false start, which
# is worth saying in the issue since it changes what the fix usually is
# (delete it rather than re-link it).
SHORT_DURATION_SECONDS = 100


class GarminLinkOnlyCheck(ActivityCheck):
    # Under active testing/development. API payload/source metadata can vary.
    # Adapt _is_garmin() / _strava_id_none() after inspecting stored raw_json
    # from your account if the heuristic misfires.
    name = "garmin_link_only"
    description = "Finds activities that appear to have Garmin-only linkage (heuristic, still being tuned)."

    def _is_garmin(self, activity):
        raw = activity.raw
        source = str(raw.get("source", "")).lower()
        external = str(raw.get("external_id", "")).lower()
        return source.startswith("garmin") or external.startswith("garmin")

    def _strava_id_none(self, activity):
        return str(activity.raw.get("strava_id")).lower() == "none"

    def run(self, activities):
        issues = []
        for a in activities:
            if not (self._is_garmin(a) and self._strava_id_none(a)):
                continue
            details = "Matched source/external_id heuristic; adapt this check to your API payload if needed."
            if (a.elapsed_time or 0) < SHORT_DURATION_SECONDS:
                details = "SHORT DURATION..." + details
            issues.append(self.issue(
                a,
                "Activity matches the configured Garmin-only linkage heuristic",
                [{"rule": "garmin_only_link", "details": details}],
                "info",
            ))
        return issues
