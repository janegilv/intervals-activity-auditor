from .base import ActivityCheck

class GarminLinkOnlyCheck(ActivityCheck):
    # Under active testing/development. API payload/source metadata can vary.
    # Adapt _is_garmin() / _strava_id_none() after inspecting cached raw_json
    # from your account if the heuristic misfires.
    name = "garmin_link_only"
    description = "Finds activities that appear to have Garmin-only linkage (heuristic, still being tuned)."

    def _is_garmin(self, activity):
        raw = activity.raw
        source = str(raw.get("source", "")).lower()
        external = str(raw.get("external_id", "")).lower()
        return source.startswith("garmin") or external.startswith("garmin")

    def _strava_id_none(self, activity):
        raw = activity.raw
        strava_id = str(raw.get("strava_id")).lower()
        return strava_id=="none"


    def run(self, activities):
        findings = []
        for a in activities:

            if self._is_garmin(a) and self._strava_id_none(a):
                # TODO: Make this a configuration item?
                # TODO: Clean up the function to avoid continue and double findings.append.
                if a.elapsed_time<100:
                  findings.append(self.finding(
                      "Activity matches the configured Garmin-only linkage heuristic",
                      [a],
                      [{"rule": "garmin_only_link",
                        "details": "SHORT DURATION...Matched source/external_id heuristic; adapt this check to your API payload if needed."}],
                      "info"
                  ))
                  continue


                findings.append(self.finding(
                    "Activity matches the configured Garmin-only linkage heuristic",
                    [a],
                    [{"rule": "garmin_only_link",
                      "details": "Matched source/external_id heuristic; adapt this check to your API payload if needed."}],
                    "info"
                ))
        return findings

#source: "GARMIN_CONNECT",
#strava_id: "REDACTED-ACTIVITY-ID",
