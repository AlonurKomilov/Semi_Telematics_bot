"""Fuel report and low-fuel alert formatters."""

from infra.context import get_company_display
from capabilities.formatting.helpers import (
    _t, _short_location, _fuel_bar, _company_tag,
    _fmt_time, _relative_ago,
)


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
                          detected_at: str | None = None) -> str:
    """Format a low fuel push alert.

    *driver_name* and *detected_at* are optional context the alerting
    pipeline now threads through so the dispatcher can decide whether
    to call the driver or route to a station — the prior format had
    only fuel% with no who/where/when context.
    """
    name = vehicle.get("name", "?")
    co = vehicle.get("_org", "")
    loc = vehicle.get("location", {})
    city = _short_location(loc)

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {_t('alert_format.low_fuel_push_title')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
    ]
    if driver_name:
        lines.append(f"  👤  {driver_name}")
    if co_label:
        lines.append(co_label.lstrip("\n"))
    if city and city != "—":
        lines.append(f"  📍  {city}")
    if detected_at:
        time_str = _fmt_time(detected_at)
        ago = _relative_ago(detected_at)
        if ago:
            time_str = f"{time_str} {ago}"
        lines.append(f"  🕐  {time_str}")
    lines.extend([
        "",
        f"  {_t('alert_format.fuel_level').replace('{pct}', f'{fuel_pct:.0f}')}",
        "",
        f"  {_t('alert_format.low_fuel_refuel')}",
    ])
    return "\n".join(lines)
