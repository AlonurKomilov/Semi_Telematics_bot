"""Fuel report and low-fuel alert formatters.

The single-alert formatter uses the unified Option A grammar — see
``capabilities/formatting/severity.py``.  The account-wide low-fuel
report (``format_low_fuel``) is a roll-up, not an alert, so it keeps
its existing layout.
"""

from capabilities.formatting.helpers import (
    _t, _short_location, _fuel_bar, _company_tag,
    _when_chip, escape_html,
)
from capabilities.formatting.severity import badge, marker


def format_low_fuel(low_fuel_vehicles: list, threshold: int,
                    show_company: bool = False) -> str:
    if not low_fuel_vehicles:
        return (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {_t('alert_format.fuel_ok_title')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {_t('alert_format.fuel_ok_msg').replace('{pct}', str(threshold))}"
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('alert_format.low_fuel_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {len(low_fuel_vehicles)} {_t('common.trucks')} < {threshold}%",
        "",
    ]

    for v in low_fuel_vehicles:
        pct = v.get("_fuel_pct", 0)
        name = v["name"]
        loc = v.get("location", {})
        city = _short_location(loc)
        bar = _fuel_bar(pct)
        tag = _company_tag(v, show_company)

        lines.append(
            f"  <b>{tag}#{name}</b>  ·  📍 {city}\n"
            f"  {bar}\n"
        )

    return "\n".join(lines)


def format_low_fuel_alert(vehicle: dict, fuel_pct: float,
                          show_company: bool = False,
                          driver_name: str | None = None,
                          detected_at: str | None = None,
                          alert_id: int | str | None = None,
                          action: str | None = None) -> str:
    """Format a low-fuel push alert in the unified Option A grammar.

    Severity is derived from the fuel percentage — under 5% reads as
    critical (range-anxiety territory), otherwise warning.
    """
    # Escape every Samsara/user-sourced string going into parse_mode=HTML.
    name = escape_html(str(vehicle.get("name", "?")))
    co = escape_html(str(vehicle.get("_org", "")))
    loc = vehicle.get("location", {})
    city = escape_html(_short_location(loc))
    driver_name = escape_html(driver_name) if driver_name else None

    sev = "critical" if fuel_pct < 5 else "warning"
    title = _t("alert_format.title_fuel")

    lines: list[str] = [f"<b>{badge(sev)}</b> — {title}", ""]

    # Stacked rows (see faults.py for the mobile-readability rationale).
    lines.append(f"🚛 <b>Vehicle #{name}</b>")
    if driver_name:
        lines.append(f"👤 {driver_name}")
    if show_company and co:
        lines.append(f"🏢 {co}")
    if city and city != "—":
        lines.append(f"📍 {city}")
    when = _when_chip(detected_at)
    if when:
        lines.append(f"🕐 {when}")

    lines.append("")
    body_marker = marker(sev)
    lines.append(f"{body_marker} <b>{fuel_pct:.0f}% fuel remaining</b>")
    lines.append("      Below low-fuel threshold")

    # Fuel-specific action override — the severity default ("Schedule
    # shop check") makes no sense for fuel.  Caller can still pass an
    # explicit ``action`` to override (e.g. with a nearest-station
    # recommendation from the routing layer).
    fuel_action = action or (
        "Refuel immediately · risk of stalling" if sev == "critical"
        else "Refuel within 50 mi"
    )
    lines.append("")
    lines.append(f"💡 {fuel_action}")
    if alert_id is not None:
        lines.append(f"🔖 #{alert_id}")

    return "\n".join(lines)
