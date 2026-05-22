"""Event alert and events dashboard formatters."""

import html as _html
from datetime import datetime
from collections import Counter
from constants import TZ_ET as _TZ_ET
from infra.context import get_company_display
from capabilities.formatting.helpers import (
    _t, _fmt_time, _relative_ago, _split_message,
)


def _esc(s: object) -> str:
    """HTML-escape a value before inlining it into a Telegram HTML message.

    Samsara-supplied strings (driver name, vehicle name, address) can
    contain ``&``, ``<``, ``>`` — Telegram's HTML parser rejects the
    whole message with ``Bad Request: can't parse entities`` and the
    bot's outer try/except swallows the error, producing the
    "video without description" symptom users reported.
    """
    return _html.escape(str(s or ""), quote=False)


def _fmt_short_et(iso_str: str) -> str:
    """Compact 'May 10, 06:15 AM ET' for footer/history use.

    The history footer used to render times in 4 US zones simultaneously;
    that's noise for fleets in a single zone (most of them).  This
    single-zone version gives the dispatcher one canonical timestamp and
    keeps the relative-ago suffix to anchor freshness.
    """
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(_TZ_ET).strftime("%b %d, %I:%M %p ET")
    except Exception:
        return iso_str


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


# Subtypes where g-force is a meaningful magnitude readout.
# Rolling stops, tailgating, lane departures, and distracted-driving
# triggers are *behavior* events without an impact magnitude — Samsara
# still emits a `maxAccelerationGForce` (often 0.00g), but surfacing it
# in the alert is misleading.
_GFORCE_EVENT_TYPES = frozenset({"crash", "braking", "acceleration", "harshTurn"})

# Words Samsara appends to behavior labels that read as accusatory in
# dispatcher UI — "Edge Railroad Crossing Violation" is already an
# unambiguous safety event, the "Violation" label adds nothing and
# sounds punitive.  These get stripped from the visible event title.
_NOISE_LABEL_SUFFIXES = ("Violation", "Detected", "Detection", "Event")


def _clean_event_name(name: str) -> str:
    """Strip noise suffix words from a Samsara behavior label."""
    cleaned = name.strip()
    # Repeated strip in case Samsara stacks two suffixes (e.g. "X Detection Event").
    for _ in range(len(_NOISE_LABEL_SUFFIXES)):
        changed = False
        for suffix in _NOISE_LABEL_SUFFIXES:
            if cleaned.endswith(" " + suffix):
                cleaned = cleaned[: -(len(suffix) + 1)].rstrip()
                changed = True
                break
        if not changed:
            break
    return cleaned or name


# Safety-event severity tiers.  Crash is always critical (any g-force);
# harsh-braking and acceleration depend on the magnitude; rolling-stop
# and following-distance are behavior info.  Mapping is conservative —
# better to under-alarm a borderline event than train users to ignore
# the badge.
_CRITICAL_EVENT_TYPES = frozenset({"crash"})
_WARNING_EVENT_TYPES = frozenset(
    {"braking", "acceleration", "harshTurn", "laneDeparture"}
)
_INFO_EVENT_TYPES = frozenset({"rollingStop", "followingDistance"})


def _event_severity(etype: str, g_force: float) -> str:
    """Pick a severity tier for a safety event.

    Crash is always critical.  Other impact-style events tip critical
    when the g-force crosses 0.6 (the "harsh / severe" threshold from
    the dashboard's gforce histogram).
    """
    if etype in _CRITICAL_EVENT_TYPES:
        return "critical"
    if etype in _GFORCE_EVENT_TYPES and g_force >= 0.6:
        return "critical"
    if etype in _WARNING_EVENT_TYPES:
        return "warning"
    if etype in _INFO_EVENT_TYPES:
        return "info"
    return "warning"


# Default action wording per event type — overrides the severity-based
# default ("Schedule shop check") which makes no sense for behavior
# coaching.  Caller can still pass an explicit ``action`` to override.
_EVENT_ACTIONS: dict[str, str] = {
    "crash":             "Confirm driver safety · dispatch response · review snapshot",
    "braking":           "Coach driver · review snapshot",
    "acceleration":      "Coach driver · review snapshot",
    "harshTurn":         "Coach driver · review snapshot",
    "laneDeparture":     "Coach driver on lane discipline",
    "rollingStop":       "Coach driver on full stops",
    "followingDistance": "Coach driver on following distance",
}


def format_event_alert(event: dict) -> str:
    """Format a single safety event in the unified Option A grammar."""
    from capabilities.formatting.severity import badge, marker

    etype = event.get("event_type", "unknown")
    g_force = event.get("g_force", 0.0)
    sev = _event_severity(etype, g_force)

    # Title — emoji + cleaned event name (e.g. "💥 Crash" / "🛑 Harsh Braking").
    type_emoji = _EVENT_EMOJI.get(etype, "🚨")
    ename = _esc(_clean_event_name(event.get("event_name", "Event")))
    title = f"{type_emoji} {ename}"

    vname = _esc(event.get("vehicle_name", "?"))
    dname = _esc(event.get("driver_name", "Unassigned"))
    lat = event.get("latitude")
    lng = event.get("longitude")

    raw_time = event.get("time", "")
    ago = _relative_ago(raw_time)
    when = ago.strip("()") if ago else _fmt_time(raw_time)

    addr = (event.get("address") or "").strip()
    if not addr:
        loc = event.get("location") or {}
        addr = ((loc.get("reverseGeo") or {}).get("formattedLocation") or "").strip()
    if addr:
        loc_str = _esc(addr)
    elif lat is not None and lng is not None:
        loc_str = f"{lat:.4f}, {lng:.4f}"
    else:
        loc_str = ""

    lines: list[str] = [f"<b>{badge(sev)}</b> — {title}", ""]

    where_parts = [f"🚛 <b>Truck #{vname}</b>"]
    if loc_str:
        where_parts.append(f"📍 {loc_str}")
    lines.append("  ·  ".join(where_parts))

    when_parts: list[str] = []
    if dname and dname != "Unassigned":
        when_parts.append(f"👤 {dname}")
    if when:
        when_parts.append(f"🕐 {when}")
    if when_parts:
        lines.append("  ·  ".join(when_parts))

    # G-force only renders for impact-style events; behavior events
    # carry no useful magnitude.
    if etype in _GFORCE_EVENT_TYPES:
        lines.append("")
        lines.append(f"{marker(sev)} <b>{g_force:.2f}g</b> deceleration / acceleration")

    lines.append("")
    lines.append(f"💡 {_EVENT_ACTIONS.get(etype, 'Coach driver · review snapshot')}")

    return "\n".join(lines)


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
            key = _EVENT_TYPE_KEYS.get(etype, "events.type_crash")
            summary_lines.append(f"  {_t(key)}: {cnt}")
    summary_lines.append("  ─────────────")
    summary_lines.append(f"  {_t('events.total').format(count=len(events))}")

    # Top 5 drivers by event count
    driver_counts = Counter(e.get("driver_name", "Unassigned") for e in events)
    top_5 = driver_counts.most_common(5)
    # Skip the leaderboard when only one driver shows up in the data
    # (e.g. a driver-role caller filtered to their own truck).  The
    # "Top Drivers" header is misleading in that case.
    driver_lines: list[str] = []
    if len(driver_counts) > 1:
        driver_lines.append(f"\n  {_t('events.top_drivers')}")
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
            display = get_company_display().get(org, org)
            company_lines.append(f"  {_t('events.company_line').format(company=display, count=cnt)}")

    full = "\n".join([header] + summary_lines + driver_lines + gforce_lines + company_lines)
    return _split_message(full)


def format_alert_history_footer(occurrence_count: int,
                                first_seen: str,
                                last_seen: str,
                                history_id: int | None = None) -> str:
    """Format a history footer for consolidated alerts.

    Shows the canonical AlertID (when known) plus the occurrence count
    and time range.  Timestamps render in a single timezone (ET, the
    company default) with a relative-ago suffix so the line stays
    readable — the prior 4-timezone display (ET / CT / MT / PT) was
    information overload for fleets that operate in one zone.
    """
    parts: list[str] = []
    # AlertID line — short, stable handle that drivers can mention in
    # chat or use to search the dashboard.  When the alert has fired
    # repeatedly we tack the occurrence count onto the same line so the
    # whole footer is two or three rows, not six.
    if history_id is not None:
        head = _t('alert_format.history_alert_id').replace('{id}', str(history_id))
        if occurrence_count > 1:
            # "× 51" alone is cryptic — readers couldn't tell it meant
            # occurrence count without context.  Use the verbose form
            # ("51 occurrences") so the chronic-pattern signal is
            # self-explanatory at a glance.
            count_label = (
                _t('alert_format.history_occurrences_inline')
                .replace('{count}', str(occurrence_count))
            )
            head = f"{head}  ·  {count_label}"
        parts.append(f"  {head}")

    if occurrence_count > 1:
        first_display = _fmt_short_et(first_seen)
        last_display = _fmt_short_et(last_seen)
        first_ago = _relative_ago(first_seen)
        last_ago = _relative_ago(last_seen)

        first_line = _t('alert_format.history_since').replace('{date}', first_display)
        if first_ago:
            first_line = f"{first_line} {first_ago}"
        last_line = _t('alert_format.history_latest').replace('{date}', last_display)
        if last_ago:
            last_line = f"{last_line} {last_ago}"
        parts.extend([f"  {first_line}", f"  {last_line}"])

    if not parts:
        return ""
    return "\n━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(parts) + "\n"
