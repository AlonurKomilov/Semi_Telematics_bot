"""Fault alert formatters."""

from __future__ import annotations

from infra.context import get_company_display
from capabilities.formatting.helpers import (
    _t, _short_location, _fmt_time, _relative_ago,
)


def format_fault_alert(
    vehicle: dict,
    new_dtcs: list,
    severity: str = "warning",
    lights: dict | None = None,
    show_company: bool = False,
    detected_at: str | None = None,
) -> str:
    """Format a fault alert for any severity tier (CRITICAL, WARNING, INFO).

    *severity* accepts a string or an ``AlertSeverity`` enum value (which is
    a ``str`` subclass, so string comparison works without importing the enum).

    * ``"critical"`` — red emoji (🔴), lights banner, critical title
    * ``"warning"``  — orange emoji (🔸), warning title
    * ``"info"``     — blue emoji (🔵), informational title
    """
    sev = str(severity).lower()
    is_critical = sev == "critical"
    is_info = sev == "info"

    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    if is_critical:
        title = _t("alert_format.critical_fault_title")
        dtc_emoji = "🔴"
    elif is_info:
        title = _t("alert_format.info_fault_title")
        dtc_emoji = "🔵"
    else:
        title = _t("alert_format.new_fault_title")
        dtc_emoji = "🔸"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {title}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        f"{co_label}",
    ]

    # Detection time — when the fault first appeared in the poll cycle.
    # The Telegram envelope timestamp is delivery time, which lags by
    # the cooldown/dedup interval; surfacing detection time anchors
    # freshness for dispatch.
    if detected_at:
        time_str = _fmt_time(detected_at)
        ago = _relative_ago(detected_at)
        if ago:
            time_str = f"{time_str} {ago}"
        lines.append(f"  🕐  {time_str}")

    if is_critical and lights:
        light_flags = []
        if lights.get("stopIsOn"):
            light_flags.append("🛑 STOP")
        if lights.get("protectIsOn"):
            light_flags.append("🟡 PROTECT")
        if lights.get("emissionsIsOn"):
            light_flags.append("⚠️ EMISSIONS")
        if lights.get("warningIsOn"):
            light_flags.append("🔶 WARNING")
        if light_flags:
            lines.append(f"\n  {'  '.join(light_flags)}")

    lines.append("")

    for dtc in new_dtcs[:5]:
        desc = dtc.get("spnDescription", "Unknown")
        sev_desc = dtc.get("fmiDescription", "Unknown")
        source = dtc.get("sourceAddressName", "Unknown")
        lines.append(
            f"  {dtc_emoji}  <b>{desc}</b>\n"
            f"       {sev_desc}\n"
            f"       {_t('alert_format.new_fault_source')} {source}\n"
        )

    # The trailing hint ("Tap 🚛 Vehicles for details") used to live
    # here.  It duplicated the inline keyboard buttons (Open in
    # Samsara / View Truck / Main Menu) and added a line of clutter,
    # so it was removed.
    return "\n".join(lines)


# ── Backward-compat aliases ──────────────────────────────────────────────────

def format_new_fault_alert(vehicle: dict, new_dtcs: list,
                           show_company: bool = False) -> str:
    """Deprecated: use format_fault_alert(severity='warning')."""
    return format_fault_alert(vehicle, new_dtcs,
                              severity="warning",
                              show_company=show_company)


def format_critical_fault_alert(vehicle: dict, new_dtcs: list,
                                lights: dict,
                                show_company: bool = False) -> str:
    """Deprecated: use format_fault_alert(severity='critical')."""
    return format_fault_alert(vehicle, new_dtcs,
                              severity="critical",
                              lights=lights,
                              show_company=show_company)
