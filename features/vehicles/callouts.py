"""The Vehicle feature's callouts — its keys, and the one fact it
detects for itself.

Two families live here:

  * the six MILEAGE caveats, which the mileage endpoints already stamp
    on their rows as ``flag`` — declared here so the vocabulary has one
    owner; the wire is untouched (the dashboard maps flag → key).
  * ``vehicle.no_engine_data``, a CONDITION detected during ingest.

The condition, and why its rule is shaped the way it is
-------------------------------------------------------
Truck 548640's gateway has power and a satellite fix but is not on the
engine bus: GPS and battery arrive, odometer/fuel/engine-hours never
do.  It reported 0 miles across 86 days while genuinely driving, so
mileage, cost-per-mile and KPI all read it as an idle asset instead of
a blind one, and the empty "—" fields read like our bug.

The rule is GPS-present + odometer-absent, NOT "n of eleven signals
missing".  Measured across this fleet: 99 of 100 telematics trucks
report odometer, engine hours, fuel, DEF and coolant — but only 92
report RPM and 95 oil pressure.  A signal-count rule would flag eight
healthy trucks, and a warning that cries wolf is one nobody reads.

Vehicles with no ``telematics_ref`` (86 of this account's 187 registry
rows are trailers and manual entries) never reach the ingest at all,
so they cannot be flagged — asserted by a test rather than trusted.
"""

from __future__ import annotations

from capabilities.callouts import register_callout

_OWNER = "vehicles"

# ── The condition ────────────────────────────────────────────────
NO_ENGINE_DATA = register_callout(
    "vehicle.no_engine_data",
    kind="condition", severity="warn", owner=_OWNER,
).key

# ── The mileage caveats ──────────────────────────────────────────
# Already stamped on mileage rows as ``flag``; declared here so the
# keys have one owner.  All are ``caveat``: they qualify a number the
# reader is looking at, and none can be dismissed — hiding "these
# miles are summed across two devices" re-hides the very thing it
# corrects.
#
# All six carry ``warn`` because all six render as a warn chip TODAY,
# and the fold into the shared catalog is deliberately rendering-
# identical — a visual diff here would be the fold's bug rather than
# its feature.  Re-tiering the three that are really informational
# ('estimated', 'catchup', 'partial') is a one-line follow-up.
for _flag, _sev in (
    ("device_change", "warn"),
    ("estimated",     "warn"),
    ("catchup",       "warn"),
    ("partial",       "warn"),
    ("reset",         "warn"),
    ("rebase",        "warn"),
):
    register_callout(
        f"mileage.{_flag}", kind="caveat", severity=_sev, owner=_OWNER,
    )

# How long the odometer may stay silent on a truck whose GPS is live
# before we call the engine bus lost.  Three days, because a truck can
# legitimately sit a long weekend without the ECU waking; the gap that
# matters here lasted 86 days.  Hardcoded on purpose — a customer
# dispute over the boundary is what would earn making it configurable.
ENGINE_DATA_GAP_HOURS = 72


def detect_no_engine_data(
    *, has_gps: bool, odometer_present: bool,
    odometer_age_hours: float | None,
) -> bool:
    """Is the engine bus lost on this vehicle, right now?

    Pure so the rule can be tested without a fleet: the caller supplies
    what the ingest tick already holds.

      has_gps            the device reported a position this tick — it
                         has power and a fix, so silence is not "off".
      odometer_present   an odometer reading arrived this tick.
      odometer_age_hours age of the newest stored reading; ``None``
                         means one has never arrived.

    A truck parked with its gateway asleep reports no GPS either, so
    requiring ``has_gps`` is what separates "not driving" from "driving
    blind".
    """
    if not has_gps or odometer_present:
        return False
    if odometer_age_hours is None:
        return True
    return odometer_age_hours >= ENGINE_DATA_GAP_HOURS
