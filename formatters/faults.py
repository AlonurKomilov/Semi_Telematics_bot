"""Fault alert formatters."""

from core.context import get_company_display
from formatters.helpers import _t, _short_location


def format_new_fault_alert(vehicle: dict, new_dtcs: list,
                           show_company: bool = False) -> str:
    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {_t('alert_format.new_fault_title')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        f"{co_label}",
        "",
    ]

    for dtc in new_dtcs[:5]:
        desc = dtc.get("spnDescription", "Unknown")
        severity = dtc.get("fmiDescription", "Unknown")
        source = dtc.get("sourceAddressName", "Unknown")
        lines.append(
            f"  🔸  <b>{desc}</b>\n"
            f"       {severity}\n"
            f"       {_t('alert_format.new_fault_source')} {source}\n"
        )

    lines.append(f"  {_t('alert_format.new_fault_hint')}")
    return "\n".join(lines)


def format_critical_fault_alert(vehicle: dict, new_dtcs: list,
                                lights: dict,
                                show_company: bool = False) -> str:
    """Format a CRITICAL fault alert (STOP/PROTECT light or Most Severe FMI)."""
    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    # Determine which lights are on
    light_flags = []
    if lights.get("stopIsOn"):
        light_flags.append("🛑 STOP")
    if lights.get("protectIsOn"):
        light_flags.append("🟡 PROTECT")
    if lights.get("emissionsIsOn"):
        light_flags.append("⚠️ EMISSIONS")
    if lights.get("warningIsOn"):
        light_flags.append("🔶 WARNING")
    lights_str = "  ".join(light_flags) if light_flags else ""

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {_t('alert_format.critical_fault_title')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        f"{co_label}",
    ]
    if lights_str:
        lines.append(f"\n  {lights_str}")
    lines.append("")

    for dtc in new_dtcs[:5]:
        desc = dtc.get("spnDescription", "Unknown")
        severity = dtc.get("fmiDescription", "Unknown")
        source = dtc.get("sourceAddressName", "Unknown")
        lines.append(
            f"  🔴  <b>{desc}</b>\n"
            f"       {severity}\n"
            f"       {_t('alert_format.new_fault_source')} {source}\n"
        )

    lines.append(f"  {_t('alert_format.critical_fault_action')}")
    return "\n".join(lines)
