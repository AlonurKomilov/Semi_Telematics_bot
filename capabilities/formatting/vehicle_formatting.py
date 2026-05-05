"""Vehicle detail and picker formatters."""

from infra.context import get_company_display
from capabilities.formatting.helpers import (
    _t, _fmt_time, _light_badges, _short_location,
    _fuel_bar, _split_message, _company_tag,
)


def format_vehicle_detail(v: dict, show_company: bool = False,
                       show_faults: bool = True) -> list[str]:
    fc = v.get("fault_codes", {})
    j1939 = fc.get("j1939", {})
    dtcs = j1939.get("diagnosticTroubleCodes", [])
    lights = j1939.get("checkEngineLights", {})
    loc = v.get("location", {})
    fuel = v.get("fuel", {})
    fuel_pct = fuel.get("value")
    co = v.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    no_device = ""
    if not v.get("has_gateway", True):
        no_device = f"\n  ⚠️  <i>{_t('vehicle.no_device')}</i>"

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🚛  <b>TRUCK  #{v['name']}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"{co_label}{no_device}",
        "",
        f"  {v['year']}  {v['make']}",
        f"  {v['model']}",
        f"  VIN    <code>{v['vin']}</code>",
        f"  Plate  {v.get('license_plate') or '—'}",
        "",
        f"  📍  {_short_location(loc)}",
        f"  {_fuel_bar(fuel_pct)}",
    ]

    if show_faults:
        lines.append("")
        lines.append(f"  💡 Dash:  {_light_badges(lights)}")

    lines.append("")
    if not show_faults:
        # Role doesn't have fault access — hide everything
        pass
    elif not dtcs:
        lines.append("  ── ── ── ── ── ── ──")
        lines.append(f"  ✅  <b>{_t('vehicle.no_faults')}</b>")
        lines.append(f"  {_t('vehicle.no_faults_note')}")
    else:
        lines.append(f"  ── ── ── ── ── ── ──")
        lines.append(f"  🔧  <b>{len(dtcs)} {_t('vehicle.active_faults')}</b>")

        for i, dtc in enumerate(dtcs, 1):
            spn = dtc.get("spnId", "?")
            fmi = dtc.get("fmiId", "?")
            spn_desc = dtc.get("spnDescription", "Unknown")
            fmi_desc = dtc.get("fmiDescription", "Unknown")
            occ = dtc.get("occurrenceCount", 0)
            source = dtc.get("sourceAddressName", "Unknown")

            fmi_lower = fmi_desc.lower()
            if "most severe" in fmi_lower:
                sev = "🔴"
            elif "moderate" in fmi_lower:
                sev = "🟠"
            elif "least severe" in fmi_lower:
                sev = "🟡"
            else:
                sev = "⚪"

            lines.append(
                f"\n  {sev}  <b>{_t('vehicle.fault_label')} #{i}</b>\n"
                f"     {_t('vehicle.code_label')}    SPN {spn} / FMI {fmi}\n"
                f"     {_t('vehicle.issue_label')}   {spn_desc}\n"
                f"     {_t('vehicle.level_label')}   {fmi_desc}\n"
                f"     {_t('vehicle.count_label')}   ×{occ}\n"
                f"     {_t('vehicle.from_label')}    {source}"
            )

    fault_time = fc.get("time", "")
    if fault_time and show_faults:
        lines.append(f"\n  🕐  Updated:  {_fmt_time(fault_time)}")

    return _split_message("\n".join(lines))


def format_vehicle_picker(vehicle_name: str, matches: list[dict]) -> str:
    """Show disambiguation when a truck name exists in multiple companies."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🔍  <b>#{vehicle_name}</b>  {_t('vehicle.found_in')}",
        f"       {len(matches)} {_t('common.companies')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('vehicle.select_which')}",
        "",
    ]
    for v in matches:
        co = v.get("_org", "?")
        name = get_company_display().get(co, co)
        lines.append(f"  • <b>{co}</b> — {name}")

    return "\n".join(lines)
