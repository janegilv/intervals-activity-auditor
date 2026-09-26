"""All available checks, by name. To add one: create a class inheriting
ActivityCheck in this package, register it here, and configure it in
checks.toml."""
from .duplicate_overlap import DuplicateOverlapCheck
from .garmin_link_only import GarminLinkOnlyCheck
from .implausible_pace import ImplausiblePaceCheck
from .missing_distance import MissingDistanceCheck
from .moving_elapsed_gap import MovingElapsedGapCheck
from .over_time_limit import OverTimeLimitCheck
from .under_time_limit import UnderTimeLimitCheck

CHECKS = {
    cls.name: cls
    for cls in (
        DuplicateOverlapCheck,
        UnderTimeLimitCheck,
        OverTimeLimitCheck,
        GarminLinkOnlyCheck,
        ImplausiblePaceCheck,
        MissingDistanceCheck,
        MovingElapsedGapCheck,
    )
}
