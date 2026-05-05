"""Fault severity classification — single source of truth.

All code that needs to know whether a vehicle's faults are CRITICAL, WARNING,
or INFO must import from here.  This prevents the classify-is-critical logic
from being copy-pasted across the samsara adapter, the alerting pipeline, the
warehouse ingestor, and the bot commands.
"""

from __future__ import annotations


def classify_is_critical(vehicle: dict) -> bool:
    """Return True when a vehicle's active faults qualify as CRITICAL.

    Critical conditions:
    * STOP warning light is on
    * PROTECT warning light is on
    * EMISSIONS warning light is on
    * Any active DTC has 'most severe' in its FMI description
    """
    lights = vehicle.get("_lights", {})
    if (
        lights.get("stopIsOn", False)
        or lights.get("protectIsOn", False)
        or lights.get("emissionsIsOn", False)
    ):
        return True
    for dtc in vehicle.get("_dtcs", []):
        if "most severe" in dtc.get("fmiDescription", "").lower():
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
