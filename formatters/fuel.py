"""Fuel report and low-fuel alert formatters."""

from core.context import get_company_display
from formatters.helpers import (
    _t, _short_location, _fuel_bar, _company_tag,
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
                          show_company: bool = False) -> str:
    """Format a low fuel push alert."""
    name = vehicle.get("name", "?")
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('alert_format.low_fuel_push_title')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  🚛  <b>Truck #{name}</b>\n"
        f"{co_label}\n"
        "\n"
        f"  {_t('alert_format.fuel_level').replace('{pct}', f'{fuel_pct:.0f}')}\n"
        "\n"
        f"  {_t('alert_format.low_fuel_refuel')}"
    )
