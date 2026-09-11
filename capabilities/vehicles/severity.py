"""Fault severity classification — single source of truth.

All code that needs to know whether a vehicle's faults are CRITICAL, WARNING,
or INFO must import from here.  This prevents the classify-is-critical logic
from being copy-pasted across the samsara adapter, the alerting pipeline, the
warehouse ingestor, and the bot commands.
"""

from __future__ import annotations


def lamps_are_critical(lights: dict | None) -> bool:
    """Return True when a check-engine-lamp payload means CRITICAL.

    Two shapes reach this code and they do not share a key.  The live
    Samsara payload names the individual lamps
    (``stopIsOn`` / ``protectIsOn`` / ``emissionsIsOn``).  The warehouse
    reader only knows a COUNT of critical DTCs, so it synthesises
    ``{"red": True}`` — red being the stop lamp's colour.  Readers that
    checked only the first spelling reported every warehouse-served
    fleet as having zero critical faults, which on a safety question is
    the worst direction to be wrong in.
    """
    lights = lights or {}
    return bool(
        lights.get("stopIsOn")
        or lights.get("protectIsOn")
        or lights.get("emissionsIsOn")
        or lights.get("red")
    )


def classify_is_critical(vehicle: dict) -> bool:
    """Return True when a vehicle's active faults qualify as CRITICAL.

    Critical conditions:
    * STOP warning light is on (either spelling — see lamps_are_critical)
    * PROTECT warning light is on
    * EMISSIONS warning light is on
    * Any active DTC has 'most severe' in its FMI description

    Accepts a vehicle carrying ``_lights`` (the faulted-vehicle shape)
    or a raw ``fault_codes.j1939.checkEngineLights`` payload, so a
    caller holding either does not have to reshape first.
    """
    lights = vehicle.get("_lights")
    if lights is None:
        lights = (
            (vehicle.get("fault_codes") or {}).get("j1939") or {}
        ).get("checkEngineLights") or {}
    if lamps_are_critical(lights):
        return True
    dtcs = vehicle.get("_dtcs")
    if dtcs is None:
        dtcs = (
            (vehicle.get("fault_codes") or {}).get("j1939") or {}
        ).get("diagnosticTroubleCodes") or []
    for dtc in dtcs:
        if "most severe" in (dtc or {}).get("fmiDescription", "").lower():
            return True
    return False


def classify_fault_severity(vehicle: dict) -> str:
    """Return 'critical', 'warning', or 'info' for a vehicle with active faults.

    Tiers:
    * critical — STOP/PROTECT/EMISSIONS light on, or 'most severe' FMI
    * warning  — any other active DTC that needs operator attention
    * info     — DTCs whose FMI description indicates lowest priority
                 ('least severe', 'condition exists', 'data erratic')
    """
    if classify_is_critical(vehicle):
        return "critical"
    for dtc in vehicle.get("_dtcs", []):
        fmi = dtc.get("fmiDescription", "").lower()
        if any(kw in fmi for kw in ("least severe", "condition exists", "data erratic",
                                    "special instructions", "not defined")):
            # All DTCs are low-priority — treat whole vehicle as INFO
            pass
        else:
            # At least one DTC is genuinely WARNING-level
            return "warning"
    return "info"
