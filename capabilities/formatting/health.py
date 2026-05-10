"""Health alert and vehicle health dashboard formatters."""

from datetime import datetime, timezone
from constants import TZ_ET as _TZ_ET
from infra.context import get_company_display
from capabilities.formatting.helpers import (
    _t, _health_icon, _company_tag, _split_message,
    _short_location, _fmt_time, _relative_ago,
)


def format_health_alert(vehicle: dict, alerts: list[str],
                        health: dict,
                        show_company: bool = False,
                        driver_name: str | None = None,
                        detected_at: str | None = None) -> str:
    """Format a vehicle health critical alert (battery, oil, coolant, DEF).

    *driver_name* and *detected_at* are optional context the alerting
    pipeline now threads through so the dispatcher knows who to call
    and how stale the reading is — the prior format had only the
    sensor reading with no who/where/when context.
    """
    name = vehicle.get("name", "?")
    co = vehicle.get("_org", "")
    loc = vehicle.get("location", {})
    city = _short_location(loc)

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {get_company_display().get(co, co)}  ({co})"

    # Severity — critical items
    is_critical = any(a in alerts for a in (
        "low_battery", "low_oil_pressure", "high_coolant_temp",
    ))
    header = _t('alert_format.health_critical') if is_critical else _t('alert_format.health_warning')

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {header}",
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
    lines.append("")

    # Show each alert condition with current value
    alert_details = {
        "low_battery": (
            _t('alert_format.health_low_battery'),
            f"{health.get('battery_v', '?')}V (threshold: 12.2V)",
        ),
        "low_oil_pressure": (
            _t('alert_format.health_low_oil'),
            f"{health.get('oil_psi', '?')} PSI (threshold: 10 PSI)",
        ),
        "high_coolant_temp": (
            _t('alert_format.health_high_coolant'),
            f"{health.get('coolant_c', '?')}°C (threshold: 105°C)",
        ),
        "low_def": (
            _t('alert_format.health_low_def'),
            f"{health.get('def_pct', '?')}% (threshold: 10%)",
        ),
        "coolant_dtc": (
            _t('alert_format.health_coolant_fault'),
            _t('alert_format.health_coolant_dtc'),
        ),
    }

    for alert_key in alerts:
        if alert_key in alert_details:
            label, detail = alert_details[alert_key]
            lines.append(f"  <b>{label}</b>\n       {detail}\n")

    return "\n".join(lines)


def format_vehicle_health(
    vehicles: list[dict],
    show_company: bool = False,
) -> list[str]:
    """Format vehicle health diagnostics for Telegram."""
    if not vehicles:
        return [
            "━━━━━━━━━━━━━━━━━━━\n"
            f"  {_t('reports.health_fmt_none')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('reports.health_fmt_no_data')}"
        ]

    alert_count = sum(len(v.get("_health_alerts", [])) for v in vehicles)
    crit_count = sum(1 for v in vehicles if v.get("_health_alerts"))
    eng_on = sum(1 for v in vehicles if v.get("_health", {}).get("engine_on"))
    eng_off = len(vehicles) - eng_on

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('reports.health_fmt_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('reports.health_scanned').replace('{count}', str(len(vehicles)))}",
        f"  {_t('reports.health_engine_on').replace('{on}', str(eng_on)).replace('{off}', str(eng_off))}",
        f"  {_t('reports.health_alerts_summary').replace('{crit}', str(crit_count)).replace('{total}', str(alert_count))}",
        "",
        "  ── ── ── ── ── ── ── ──",
        "",
    ]

    for v in vehicles:
        name = v["name"]
        h = v.get("_health", {})
        alerts = v.get("_health_alerts", [])
        tag = _company_tag(v, show_company)
        icon = _health_icon(alerts)
        eng = "🟢ON" if h.get("engine_on") else "⚫️OFF"

        parts = []
        if "battery_v" in h:
            bv = h["battery_v"]
            flag = " ⚠️" if bv < 12.2 else ""
            parts.append(f"🔋{bv:.1f}V{flag}")
        if "oil_psi" in h:
            op = h["oil_psi"]
            flag = " ⚠️" if op < 10 else ""
            parts.append(f"🛢{op:.0f}psi{flag}")
        if "coolant_c" in h:
            cc = h["coolant_c"]
            flag = " ⚠️" if cc > 105 else ""
            parts.append(f"🌡{cc:.0f}°C{flag}")
        if "def_pct" in h:
            dp = h["def_pct"]
            flag = " ⚠️" if dp < 10 else ""
            parts.append(f"💧DEF {dp:.0f}%{flag}")

        detail = "  ·  ".join(parts) if parts else _t('common.no_data')

        # Compute freshness from most recent sensor timestamp
        fresh = ""
        time_vals = [h[k] for k in h if k.endswith("_time") and h[k]]
        if time_vals:
            latest = max(time_vals)
            try:
                dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                mins = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
                if mins < 60:
                    fresh = f"  ·  🕐 {mins}m ago"
                elif mins < 1440:
                    fresh = f"  ·  🕐 {mins // 60}h ago"
                else:
                    fresh = f"  ·  🕐 {mins // 1440}d ago"
            except (ValueError, TypeError):
                pass

        lines.append(f"  {icon} <b>{tag}#{name}</b>  {eng}{fresh}\n  {detail}\n")

    now_et = datetime.now(_TZ_ET)
    lines.append(f"  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} EST")

    return _split_message("\n".join(lines))
