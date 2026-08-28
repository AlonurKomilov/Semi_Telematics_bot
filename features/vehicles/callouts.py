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

from capabilities.callouts import register_callout, register_detector

_OWNER = "vehicles"

# ── The condition ────────────────────────────────────────────────
NO_ENGINE_DATA = register_callout(
    "vehicle.no_engine_data",
    kind="condition", severity="warn", owner=_OWNER,
).key

# ── The truck has left the fleet ─────────────────────────────────
# Not a fault and not a question — a STATE, stated once at the top of
# a page that otherwise looks exactly like a working truck's.  Without
# it the detail page shows four-month-old fuel, DEF and coordinates
# with a freshness dot beside them, which reads as current data to
# anyone who does not already know the truck was retired.
#
# `condition`, so it collapses rather than being dismissable: the fact
# stays true for as long as the truck is archived, and hiding it is
# what let the page mislead in the first place.  `info`, not `warn` —
# nothing is wrong here, someone decided this (or the sweep did), and
# painting it amber would put a retired truck in the same visual
# register as a fault.
# TWO keys, because they are two different facts and one sentence
# cannot honestly say both.  "Someone retired this truck" is a
# decision; "its gateway went silent and we retired the row" might be
# a broken device someone should go and look at.  Collapsing them into
# one statement would hide the second, which is the one with an
# action attached.
ARCHIVED = register_callout(
    "vehicle.archived",
    kind="condition", severity="info", owner=_OWNER,
).key
STOPPED_REPORTING = register_callout(
    "vehicle.stopped_reporting",
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

# ── Device identity questions ────────────────────────────────────
# The identity watch records a CHANGE behind a provider id — a VIN
# that now names a different truck, a swapped gateway, an odometer
# that re-based.  Those are questions for a human whose answer edits
# the registry, so they keep their own store (``device_event_log``)
# and their own resolution flow; only the DISPLAY comes through the
# callouts lane, so one page does not carry two shapes of statement.
#
# Never dismissible: a question that changes data is ANSWERED, not
# hidden.  The card supplies its own buttons through the strip's
# actions slot.
for _kind, _sev in (
    ("vin_changed",       "danger"),
    ("gateway_swapped",   "warn"),
    ("odometer_rebased",  "warn"),
):
    register_callout(
        f"vehicle.{_kind}", kind="condition", severity=_sev, owner=_OWNER,
    )

# ``device_event_log.kind`` → the callout key that renders it.  The
# event vocabulary predates the lane and is stored in thousands of
# rows, so it is mapped rather than renamed.
EVENT_CALLOUT_KEYS = {
    "vin_change":   "vehicle.vin_changed",
    "gateway_swap": "vehicle.gateway_swapped",
    "odo_rebase":   "vehicle.odometer_rebased",
}


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


def _detect_from_row(row: dict, prior: dict) -> dict | None:
    """The ingest's per-vehicle question, answered with vehicle
    knowledge that belongs to this feature rather than to the tick.

    ``row`` is the live-state row being written this tick; ``prior`` is
    what the warehouse held for that vehicle before it.  Returns the
    callout's params when the bus is silent, else ``None``.
    """
    from datetime import datetime, timezone

    has_gps = row.get("lat") is not None and row.get("lon") is not None
    odo_now = row.get("odometer_mi") is not None

    age: float | None = 0.0 if odo_now else None
    if not odo_now:
        stamp = row.get("odometer_time") or prior.get("odometer_time")
        if stamp:
            try:
                t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                age = max(
                    0.0,
                    (datetime.now(timezone.utc) - t).total_seconds() / 3600.0,
                )
            except ValueError:
                age = None

    if not detect_no_engine_data(
        has_gps=has_gps, odometer_present=odo_now, odometer_age_hours=age,
    ):
        return None
    return {"gateway": str(row.get("gateway_serial") or "")}


register_detector(NO_ENGINE_DATA, _detect_from_row)
