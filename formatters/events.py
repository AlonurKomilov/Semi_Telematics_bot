"""Event alert and events dashboard formatters."""

from datetime import datetime
from collections import Counter
from constants import TZ_ET as _TZ_ET
from samsara_client import COMPANY_DISPLAY
from formatters.helpers import (
    _t, _fmt_time, _fmt_us_times, _split_message,
)


_EVENT_EMOJI: dict[str, str] = {
    "crash": "💥",
    "braking": "🛑",
    "rollingStop": "↩️",
    "followingDistance": "🚗",
    "harshTurn": "🔄",
    "laneDeparture": "↔️",
    "acceleration": "🏎️",
}

_EVENT_TYPE_KEYS: dict[str, str] = {
    "crash": "events.type_crash",
    "braking": "events.type_braking",
    "rollingStop": "events.type_rolling_stop",
    "followingDistance": "events.type_following",
    "harshTurn": "events.type_harsh_turn",
    "laneDeparture": "events.type_lane_departure",
    "acceleration": "events.type_acceleration",
}


def format_event_alert(event: dict) -> str:
    """Format a single event for push notification (HTML)."""
    etype = event.get("event_type", "unknown")
    emoji = _EVENT_EMOJI.get(etype, "🚨")
    ename = event.get("event_name", "Event")
    vname = event.get("vehicle_name", "?")
    dname = event.get("driver_name", "Unassigned")
    gf = event.get("g_force", 0.0)
    lat = event.get("latitude")
    lng = event.get("longitude")
    time_str = _fmt_time(event.get("time", ""))

    loc_str = f"{lat:.4f}, {lng:.4f}" if lat is not None and lng is not None else "—"

    return (
        f"{emoji} <b>{ename}</b>\n\n"
        f"  🚛 {_t('events.vehicle_label')}: <b>{vname}</b>\n"
        f"  👤 {_t('events.driver_label')}: <b>{dname}</b>\n"
        f"  ⚡ {_t('events.gforce_label')}: <b>{gf:.2f}g</b>\n"
        f"  📍 {_t('events.location_label')}: {loc_str}\n"
        f"  🕐 {_t('events.time_label')}: {time_str}\n"
    )


def format_events_dashboard(
    events: list[dict], days: int, company_label: str = "",
) -> list[str]:
    """Format multi-event dashboard for Telegram text output."""
    now_et = datetime.now(_TZ_ET)

    header = (
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"  🚨  <b>{_t('events.title')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n  {company_label}\n"
        f"  {_t('events.period_label').format(days=days)}\n"
        f"  {now_et:%b %d, %Y %I:%M %p ET}\n"
    )

    # Event type summary
    type_counts = Counter(e.get("event_type", "unknown") for e in events)
    type_order = ["crash", "braking", "rollingStop", "followingDistance",
                  "harshTurn", "laneDeparture", "acceleration"]

    summary_lines = [f"\n  {_t('events.summary_header')}"]
    for etype in type_order:
        cnt = type_counts.get(etype, 0)
        if cnt > 0:
            emoji = _EVENT_EMOJI.get(etype, "🚨")
            key = _EVENT_TYPE_KEYS.get(etype, "events.type_crash")
            summary_lines.append(f"  {_t(key)}: {cnt}")
    summary_lines.append(f"  ─────────────")
    summary_lines.append(f"  {_t('events.total').format(count=len(events))}")

    # Top 5 drivers by event count
    driver_counts = Counter(e.get("driver_name", "Unassigned") for e in events)
    top_5 = driver_counts.most_common(5)
    driver_lines = [f"\n  {_t('events.top_drivers')}"]
    for dname, cnt in top_5:
        # Find most common event type for this driver
        driver_events = [e.get("event_type", "") for e in events if e.get("driver_name") == dname]
        top_type = Counter(driver_events).most_common(1)[0][0] if driver_events else ""
        type_label = _EVENT_EMOJI.get(top_type, "")
        driver_lines.append(f"  👤 {dname}: {cnt} events {type_label}")

    # G-force distribution
    gforce_lines = [f"\n  {_t('events.gforce_header')}"]
    mild = sum(1 for e in events if e.get("g_force", 0) < 0.4)
    moderate = sum(1 for e in events if 0.4 <= e.get("g_force", 0) < 0.6)
    harsh = sum(1 for e in events if 0.6 <= e.get("g_force", 0) < 0.8)
    severe = sum(1 for e in events if e.get("g_force", 0) >= 0.8)
    gforce_lines.append(f"  {_t('events.gforce_mild').format(count=mild)}")
    gforce_lines.append(f"  {_t('events.gforce_moderate').format(count=moderate)}")
    gforce_lines.append(f"  {_t('events.gforce_harsh').format(count=harsh)}")
    gforce_lines.append(f"  {_t('events.gforce_severe').format(count=severe)}")

    # Company breakdown (if multi-org)
    org_counts = Counter(e.get("_org", "") for e in events)
    company_lines = []
    if len(org_counts) > 1:
        company_lines.append(f"\n  {_t('events.company_header')}")
        for org, cnt in org_counts.most_common():
            display = COMPANY_DISPLAY.get(org, org)
            company_lines.append(f"  {_t('events.company_line').format(company=display, count=cnt)}")

    full = "\n".join([header] + summary_lines + driver_lines + gforce_lines + company_lines)
    return _split_message(full)


def format_alert_history_footer(occurrence_count: int,
                                first_seen: str,
                                last_seen: str) -> str:
    """Format a history footer for consolidated alerts.

    Shows occurrence count and time range when count > 1.
    Timestamps displayed in all 4 major US time zones.
    """
    if occurrence_count <= 1:
        return ""

    first_display = _fmt_us_times(first_seen)
    last_display = _fmt_us_times(last_seen)

    return (
        "\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('alert_format.history_footer_title')}\n"
        f"  {_t('alert_format.history_occurrences').replace('{count}', str(occurrence_count))}\n"
        f"  {_t('alert_format.history_since').replace('{date}', first_display)}\n"
        f"  {_t('alert_format.history_latest').replace('{date}', last_display)}\n"
    )
