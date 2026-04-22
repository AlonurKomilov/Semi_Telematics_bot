"""Fleet weather conditions formatter."""

from datetime import datetime
from constants import TZ_ET as _TZ_ET
from capabilities.formatting.helpers import (
    _t, _temp_icon, _company_tag, _short_location, _split_message,
)


def format_fleet_weather(
    vehicles: list[dict],
    show_company: bool = False,
) -> list[str]:
    """Format fleet weather conditions for Telegram."""
    if not vehicles:
        return [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {_t('reports.weather_none')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('reports.weather_none_msg')}"
        ]

    temps = [v["_weather"]["temp_f"] for v in vehicles
             if v.get("_weather", {}).get("temp_f") is not None]
    freezing = sum(1 for t in temps if t <= 32)
    hot = sum(1 for t in temps if t >= 100)
    avg_temp = sum(temps) / len(temps) if temps else 0

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('reports.weather_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('reports.weather_reporting').replace('{count}', str(len(vehicles)))}",
        f"  {_t('reports.weather_avg').replace('{temp}', f'{avg_temp:.0f}')}",
    ]
    if freezing:
        lines.append(f"  {_t('reports.weather_freezing').replace('{count}', str(freezing))}")
    if hot:
        lines.append(f"  {_t('reports.weather_hot').replace('{count}', str(hot))}")
    lines.extend(["", "  ── ── ── ── ── ── ── ──", ""])

    for v in vehicles:
        w = v.get("_weather", {})
        name = v["name"]
        tag = _company_tag(v, show_company)
        temp_f = w.get("temp_f")
        icon = _temp_icon(temp_f)

        loc = v.get("location", {})
        city = _short_location(loc)

        if temp_f is not None:
            temp_str = f"{temp_f:.0f}°F"
            warn = ""
            if temp_f <= 32:
                warn = " ⚠️"
            elif temp_f >= 100:
                warn = " ⚠️"
        else:
            temp_str = "N/A"
            warn = ""

        lines.append(f"  {icon} <b>{tag}#{name}</b>  {temp_str}{warn}  ·  📍 {city}")

    now_et = datetime.now(_TZ_ET)
    lines.append(f"\n  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} EST")

    return _split_message("\n".join(lines))
