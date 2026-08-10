"""Health alert and vehicle health dashboard formatters.

The single-alert formatter uses the unified Option A grammar — see
``capabilities/formatting/severity.py``.  The dashboard formatter
(``format_vehicle_health``) is a report, not an alert, so it keeps its
existing tabular layout.
"""

from datetime import datetime, timezone
from constants import TZ_ET as _TZ_ET
from capabilities.formatting.helpers import (
    _t, _health_icon, _company_tag, _split_message,
    _short_location, _when_chip, escape_html,
)
from capabilities.formatting.severity import badge, marker, default_action


def format_health_alert(vehicle: dict, alerts: list[str],
                        health: dict,
                        show_company: bool = False,
                        driver_name: str | None = None,
                        detected_at: str | None = None,
                        alert_id: int | str | None = None,
                        action: str | None = None) -> str:
    """Format a vehicle health alert in the unified Option A grammar.

    Severity is derived from the alert keys — battery / oil / coolant
    issues are critical; low DEF and coolant DTCs are warnings.
    """
    # See faults.py for the escape_html rationale — every
    # Samsara-sourced or user-supplied string going into parse_mode=HTML
    # is escaped at this boundary.
    name = escape_html(str(vehicle.get("name", "?")))
    co = escape_html(str(vehicle.get("_org", "")))
    loc = vehicle.get("location", {})
    city = escape_html(_short_location(loc))
    driver_name = escape_html(driver_name) if driver_name else None

    is_critical = any(a in alerts for a in (
        "low_battery", "low_oil_pressure", "high_coolant_temp",
    ))
    sev = "critical" if is_critical else "warning"
    title = _t("alert_format.title_health")

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

    # Per-condition body rows.  Plain English label + the sensor
    # reading vs threshold so the user can judge how bad it is at a
    # glance ("26 psi at idle (threshold 30)" reads as "barely under";
    # "8 psi" as "engine is in trouble right now").
    label_map = {
        "low_battery": ("Low Battery",
                        f"{health.get('battery_v', '?')}V (threshold 12.2V)"),
        "low_oil_pressure": ("Low Oil Pressure",
                             f"{health.get('oil_psi', '?')} psi (threshold 10 psi)"),
        "high_coolant_temp": ("High Coolant Temperature",
                              f"{health.get('coolant_c', '?')}°C (threshold 105°C)"),
        "low_def": ("Low DEF Level",
                    f"{health.get('def_pct', '?')}% (threshold 10%)"),
        "coolant_dtc": ("Coolant System Fault", "J1939 DTC active"),
    }

    body_marker = marker(sev)
    for key in alerts:
        if key in label_map:
            label, detail = label_map[key]
            lines.append(f"{body_marker} <b>{label}</b>")
            lines.append(f"      {detail}")

    lines.append("")
    lines.append(f"💡 {action or default_action(sev)}")
    if alert_id is not None:
        lines.append(f"🔖 #{alert_id}")

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
    # tzname() says EDT or EST honestly — the literal "EST" was wrong
    # for the ~8 months of daylight time.
    lines.append(
        f"  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} {now_et.tzname()}")

    return _split_message("\n".join(lines))
