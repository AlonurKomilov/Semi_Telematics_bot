"""Fault alert formatters.

Grammar
───────
One visual language for every alert (see ``severity.py``):

    <BADGE> <SEVERITY> — Fault Detected

    🚛 Truck #143  ·  📍 San Antonio, TX
    🏢 PTG  ·  🕐 just now

    🔸 <What>
          <plain-language impact>
          <technical detail in parens>

    💡 <Action>
    🔖 #<alert id>

The technical detail (SPN/FMI, source controller) stays in parentheses
on the third body line so mechanics still see it, but the headline +
plain-language impact + action serve everyone else.
"""

from __future__ import annotations

from capabilities.formatting.helpers import (
    _t, _short_location, _fmt_time, _relative_ago, escape_html,
)
from capabilities.formatting.severity import badge, marker, default_action


def _carrier_chip(co: str, show_company: bool) -> str:
    """Render the carrier as a compact chip (just the code if known,
    full name when not).  The prior format printed both — long and
    redundant once the user learned the carrier code.
    """
    if not show_company or not co:
        return ""
    return f"🏢 {co}"


def _when_chip(detected_at: str | None) -> str:
    """Time line, preferring the relative form for fresh alerts.

    ``just now`` / ``4 min ago`` is what dispatch actually scans for;
    the absolute timestamp only matters for follow-up — and it's still
    available on the Samsara link button below the message.
    """
    if not detected_at:
        return ""
    ago = _relative_ago(detected_at)
    if ago:
        return f"🕐 {ago.strip('()')}"
    return f"🕐 {_fmt_time(detected_at)}"


def format_fault_alert(
    vehicle: dict,
    new_dtcs: list,
    severity: str = "warning",
    lights: dict | None = None,
    show_company: bool = False,
    detected_at: str | None = None,
    alert_id: int | str | None = None,
    action: str | None = None,
) -> str:
    """Format a fault alert in the unified Option A grammar.

    *severity* accepts a string or an ``AlertSeverity`` enum value
    (string subclass) — normalised via ``severity.normalize``.

    *action* is an optional rule-specific recommendation that replaces
    the per-severity default ("Schedule shop check · drive normally").
    Callers with a per-DTC rule pass it; everyone else gets the default.

    *alert_id* is the public alert id (e.g. 2244) rendered as the
    closing chip; ``None`` skips the chip — useful for previews.
    """
    sev = str(severity).lower()
    is_critical = sev == "critical"

    # Every Samsara-sourced string flowing into ``<b>…</b>`` /
    # parse_mode=HTML must pass through ``escape_html`` or Telegram's
    # parser breaks on stray ``<``/``>``/``&`` in DTC descriptions,
    # geocoded addresses, or controller names.  Defensive everywhere
    # rather than per-field — cost is negligible.
    name = escape_html(str(vehicle.get("name", "?")))
    loc = vehicle.get("location", {})
    city = escape_html(_short_location(loc))
    co = escape_html(str(vehicle.get("_org", "")))
    title = _t("alert_format.title_fault")

    # ── Header: badge — title ─────────────────────────────────────
    lines: list[str] = [f"<b>{badge(sev)}</b> — {title}", ""]

    # ── Where + when, two lines max ──────────────────────────────
    where_parts = [f"🚛 <b>Truck #{name}</b>"]
    if city and city != "—":
        where_parts.append(f"📍 {city}")
    lines.append("  ·  ".join(where_parts))

    when_parts: list[str] = []
    carrier = _carrier_chip(co, show_company)
    if carrier:
        when_parts.append(carrier)
    when = _when_chip(detected_at)
    if when:
        when_parts.append(when)
    if when_parts:
        lines.append("  ·  ".join(when_parts))

    # ── Critical-only lights banner ──────────────────────────────
    # Only the truly red conditions get a lights row; warning/info
    # don't — the banner is a "the truck is telling you to act NOW"
    # signal that loses meaning if it shows on every alert.
    if is_critical and lights:
        flags: list[str] = []
        if lights.get("stopIsOn"):
            flags.append("🛑 STOP")
        if lights.get("protectIsOn"):
            flags.append("🟡 PROTECT")
        if lights.get("emissionsIsOn"):
            flags.append("⚠️ EMISSIONS")
        if lights.get("warningIsOn"):
            flags.append("🔶 WARNING")
        if flags:
            lines.append("")
            lines.append("   ".join(flags))

    lines.append("")

    # ── Body: per-DTC row, capped at five ────────────────────────
    body_marker = marker(sev)
    dtcs = new_dtcs[:5]
    if len(dtcs) >= 3:
        # 3+ DTCs: collapse to one-liner each + a hint.  The
        # ``(Brakes controller · Low FMI)`` parenthetical lives only
        # on the first row to avoid five identical tails.
        for i, dtc in enumerate(dtcs):
            desc = escape_html(str(dtc.get("spnDescription", "Unknown")))
            tail = ""
            if i == 0:
                sev_desc = escape_html(str(dtc.get("fmiDescription", "")))
                source = escape_html(str(dtc.get("sourceAddressName", "")))
                tail_bits = [b for b in (sev_desc, source) if b]
                if tail_bits:
                    tail = f"  ({' · '.join(tail_bits)})"
            lines.append(f"{body_marker} <b>{desc}</b>{tail}")
    else:
        for dtc in dtcs:
            desc = escape_html(str(dtc.get("spnDescription", "Unknown")))
            sev_desc = escape_html(str(dtc.get("fmiDescription", "Unknown")))
            source = escape_html(str(dtc.get("sourceAddressName", "Unknown")))
            lines.append(f"{body_marker} <b>{desc}</b>")
            lines.append(f"      {sev_desc}")
            lines.append(f"      ({source})")

    # ── Action + id chip ─────────────────────────────────────────
    lines.append("")
    lines.append(f"💡 {action or default_action(sev)}")
    if alert_id is not None:
        lines.append(f"🔖 #{alert_id}")

    return "\n".join(lines)


# ── Backward-compat aliases ──────────────────────────────────────────

def format_new_fault_alert(vehicle: dict, new_dtcs: list,
                           show_company: bool = False) -> str:
    """Deprecated: use ``format_fault_alert(severity='warning')``."""
    return format_fault_alert(vehicle, new_dtcs,
                              severity="warning",
                              show_company=show_company)


def format_critical_fault_alert(vehicle: dict, new_dtcs: list,
                                lights: dict,
                                show_company: bool = False) -> str:
    """Deprecated: use ``format_fault_alert(severity='critical')``."""
    return format_fault_alert(vehicle, new_dtcs,
                              severity="critical",
                              lights=lights,
                              show_company=show_company)
