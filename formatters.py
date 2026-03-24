"""
Telegram message formatters — clean, rich, easy-to-read fleet reports.
Uses HTML parse mode.  Multi-company aware.  Role-aware help menus.
"""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

_TZ_ET = ZoneInfo("America/New_York")

from samsara_client import COMPANY_DISPLAY


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        et = dt.astimezone(_TZ_ET)
        return et.strftime("%b %d, %Y  %I:%M %p")
    except Exception:
        return iso_str


def _light_badges(lights: dict) -> str:
    badges = []
    if lights.get("stopIsOn"):
        badges.append("🛑 STOP")
    if lights.get("protectIsOn"):
        badges.append("🛡 PROTECT")
    if lights.get("emissionsIsOn"):
        badges.append("♨️ EMIS")
    if lights.get("warningIsOn"):
        badges.append("⚠️ WARN")
    return "  ".join(badges) if badges else "✅ All Clear"


def _severity_rank(vehicle: dict) -> int:
    lights = vehicle.get("_lights", {})
    if lights.get("stopIsOn"):
        return 0
    if lights.get("protectIsOn"):
        return 1
    if lights.get("emissionsIsOn"):
        return 2
    if lights.get("warningIsOn"):
        return 3
    return 4


def _short_location(loc: dict) -> str:
    if not loc:
        return "—"
    reverse = loc.get("reverseGeo", {})
    addr = reverse.get("formattedLocation", "")
    if addr:
        parts = [p.strip() for p in addr.split(",")]
        if len(parts) >= 3:
            return f"{parts[-3]}, {parts[-2].strip()}"
        if len(parts) >= 2:
            return f"{parts[0]}, {parts[1].strip()}"
        return addr
    return "—"


def _fuel_bar(pct) -> str:
    if pct is None:
        return "⛽ —"
    filled = round(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    if pct <= 15:
        return f"🔴 {bar} {pct}%"
    if pct <= 25:
        return f"🟡 {bar} {pct}%"
    return f"🟢 {bar} {pct}%"


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _company_tag(v: dict, show_company: bool) -> str:
    """Return '[PTG] ' prefix when multi-company context."""
    if not show_company:
        return ""
    co = v.get("_org", "")
    return f"[{co}] " if co else ""


# ═══════════════════════════════════════════════════════════════════
#  /start  and  /help
# ═══════════════════════════════════════════════════════════════════

def format_help(company_codes: list[str] | None = None,
                user=None, account=None) -> str:
    """Build the /start help text.

    If `user` (database.User) and `account` (database.Account) are given,
    show personalised role-aware info.  Falls back to the original
    generic text when called without those args (backwards compat).
    """
    # Role badge
    role_line = ""
    acct_line = ""
    if user and account:
        from permissions import role_display
        role_line = f"\n  {role_display(user.role)}  ·  {account.name}\n"
    elif account:
        acct_line = f"\n  🏢 {account.name}\n"

    company_line = ""
    has_api = bool(company_codes)
    if company_codes and len(company_codes) > 1:
        names = [f"{c} ({COMPANY_DISPLAY.get(c, c)})" for c in company_codes]
        company_line = (
            "\n"
            "  🏢 Companies:\n"
            "  " + "  ·  ".join(names) + "\n"
        )

    # API status hint
    if has_api:
        status_line = "  📡 Samsara API connected ✅\n"
    else:
        status_line = "  📡 Samsara API not connected\n"

    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  🚛  <b>Semi Telematics</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Real-time fleet monitoring\n"
        "powered by Samsara\n"
        f"{role_line}{acct_line}{company_line}"
        "\n"
        f"{status_line}"
        "\n"
        "▸ <b>Tap a button below to begin</b> ▾\n"
    )


# ═══════════════════════════════════════════════════════════════════
#  /truck <number>  —  Single Truck Detail
# ═══════════════════════════════════════════════════════════════════

def format_truck_detail(v: dict, show_company: bool = False,
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
        co_label = f"\n  🏢  {COMPANY_DISPLAY.get(co, co)}  ({co})"

    no_device = ""
    if not v.get("has_gateway", True):
        no_device = "\n  ⚠️  <i>No Samsara device — live data limited</i>"

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
        lines.append("  ✅  <b>No active fault codes</b>")
        lines.append("  Truck is running clean!")
    else:
        lines.append(f"  ── ── ── ── ── ── ──")
        lines.append(f"  🔧  <b>{len(dtcs)} Active Fault{'s' if len(dtcs) != 1 else ''}</b>")

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
                f"\n  {sev}  <b>Fault #{i}</b>\n"
                f"     Code    SPN {spn} / FMI {fmi}\n"
                f"     Issue   {spn_desc}\n"
                f"     Level   {fmi_desc}\n"
                f"     Count   ×{occ}\n"
                f"     From    {source}"
            )

    fault_time = fc.get("time", "")
    if fault_time and show_faults:
        lines.append(f"\n  🕐  Updated:  {_fmt_time(fault_time)}")

    return _split_message("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  Truck Picker — when name matches multiple companies
# ═══════════════════════════════════════════════════════════════════

def format_truck_picker(truck_name: str, matches: list[dict]) -> str:
    """Show disambiguation when a truck name exists in multiple companies."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🔍  <b>#{truck_name}</b>  found in",
        f"       {len(matches)} companies",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        "  Select which one:",
        "",
    ]
    for v in matches:
        co = v.get("_org", "?")
        name = COMPANY_DISPLAY.get(co, co)
        lines.append(f"  • <b>{co}</b> — {name}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  /fuel  —  Low Fuel Alert
# ═══════════════════════════════════════════════════════════════════

def format_low_fuel(low_fuel_vehicles: list, threshold: int,
                    show_company: bool = False) -> str:
    if not low_fuel_vehicles:
        return (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ✅  <b>FUEL OK</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  All trucks above {threshold}% fuel."
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  ⛽  <b>LOW FUEL ALERT</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {len(low_fuel_vehicles)} truck{'s' if len(low_fuel_vehicles) != 1 else ''} below {threshold}%",
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


# ═══════════════════════════════════════════════════════════════════
#  Auto-Alert  —  New Fault Notification
# ═══════════════════════════════════════════════════════════════════

def format_new_fault_alert(vehicle: dict, new_dtcs: list,
                           show_company: bool = False) -> str:
    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {COMPANY_DISPLAY.get(co, co)}  ({co})"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "  🚨  <b>NEW FAULT DETECTED</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        f"{co_label}",
        "",
    ]

    for dtc in new_dtcs[:5]:
        desc = dtc.get("spnDescription", "Unknown")
        severity = dtc.get("fmiDescription", "Unknown")
        source = dtc.get("sourceAddressName", "Unknown")
        lines.append(
            f"  🔸  <b>{desc}</b>\n"
            f"       {severity}\n"
            f"       Source: {source}\n"
        )

    lines.append(f"  ▸  Tap 🚛 Search Truck for details")
    return "\n".join(lines)


def format_critical_fault_alert(vehicle: dict, new_dtcs: list,
                                lights: dict,
                                show_company: bool = False) -> str:
    """Format a CRITICAL fault alert (STOP/PROTECT light or Most Severe FMI)."""
    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {COMPANY_DISPLAY.get(co, co)}  ({co})"

    # Determine which lights are on
    light_flags = []
    if lights.get("stopIsOn"):
        light_flags.append("🛑 STOP")
    if lights.get("protectIsOn"):
        light_flags.append("🟡 PROTECT")
    if lights.get("emissionsIsOn"):
        light_flags.append("⚠️ EMISSIONS")
    if lights.get("warningIsOn"):
        light_flags.append("🔶 WARNING")
    lights_str = "  ".join(light_flags) if light_flags else ""

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "  🛑  <b>CRITICAL FAULT</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        f"{co_label}",
    ]
    if lights_str:
        lines.append(f"\n  {lights_str}")
    lines.append("")

    for dtc in new_dtcs[:5]:
        desc = dtc.get("spnDescription", "Unknown")
        severity = dtc.get("fmiDescription", "Unknown")
        source = dtc.get("sourceAddressName", "Unknown")
        lines.append(
            f"  🔴  <b>{desc}</b>\n"
            f"       {severity}\n"
            f"       Source: {source}\n"
        )

    lines.append("  ⚠️ <b>Immediate attention required</b>")
    return "\n".join(lines)


def format_health_alert(vehicle: dict, alerts: list[str],
                        health: dict,
                        show_company: bool = False) -> str:
    """Format a vehicle health critical alert (battery, oil, coolant, DEF)."""
    name = vehicle.get("name", "?")
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {COMPANY_DISPLAY.get(co, co)}  ({co})"

    # Severity — critical items
    is_critical = any(a in alerts for a in (
        "low_battery", "low_oil_pressure", "high_coolant_temp",
    ))
    header = "CRITICAL HEALTH" if is_critical else "HEALTH WARNING"
    icon = "🛑" if is_critical else "⚠️"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {icon}  <b>{header}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"{co_label}",
        "",
    ]

    # Show each alert condition with current value
    alert_details = {
        "low_battery": (
            "🔋", "Low Battery",
            f"{health.get('battery_v', '?')}V (threshold: 12.2V)",
        ),
        "low_oil_pressure": (
            "🛢", "Low Oil Pressure",
            f"{health.get('oil_psi', '?')} PSI (threshold: 10 PSI)",
        ),
        "high_coolant_temp": (
            "🌡", "High Coolant Temp",
            f"{health.get('coolant_c', '?')}°C (threshold: 105°C)",
        ),
        "low_def": (
            "💧", "Low DEF Level",
            f"{health.get('def_pct', '?')}% (threshold: 10%)",
        ),
        "coolant_dtc": (
            "🌡", "Coolant System Fault",
            "J1939 coolant DTC detected",
        ),
    }

    for alert_key in alerts:
        if alert_key in alert_details:
            emoji, label, detail = alert_details[alert_key]
            lines.append(f"  {emoji}  <b>{label}</b>\n       {detail}\n")

    return "\n".join(lines)


def format_low_fuel_alert(vehicle: dict, fuel_pct: float,
                          show_company: bool = False) -> str:
    """Format a low fuel push alert."""
    name = vehicle.get("name", "?")
    co = vehicle.get("_org", "")

    co_label = ""
    if show_company and co:
        co_label = f"\n  🏢  {COMPANY_DISPLAY.get(co, co)}  ({co})"

    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⛽  <b>LOW FUEL ALERT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  🚛  <b>Truck #{name}</b>\n"
        f"{co_label}\n"
        "\n"
        f"  ⛽ Fuel Level: <b>{fuel_pct:.0f}%</b>\n"
        "\n"
        f"  Truck needs refueling soon."
    )


def format_alert_history_footer(occurrence_count: int,
                                first_seen: str,
                                last_seen: str) -> str:
    """Format a history footer for consolidated alerts.

    Shows occurrence count and time range when count > 1.
    """
    if occurrence_count <= 1:
        return ""

    # Parse first/last timestamps for human display
    first_short = first_seen[5:16].replace("T", " ") if len(first_seen) > 16 else first_seen
    last_short = last_seen[5:16].replace("T", " ") if len(last_seen) > 16 else last_seen

    return (
        "\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"  📊 <b>Alert History</b>\n"
        f"  Occurrences: <b>{occurrence_count}</b>\n"
        f"  Since: {first_short}\n"
        f"  Latest: {last_short}\n"
    )


# ═══════════════════════════════════════════════════════════════════
#  Registration / Account Management Formatters
# ═══════════════════════════════════════════════════════════════════

def format_welcome_unregistered(support_contact: str = "") -> str:
    """Shown to users who haven't registered or joined yet."""
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  🚛  <b>Semi Telematics Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Your all-in-one fleet management\n"
        "assistant, right inside Telegram.\n"
        "\n"
        "<b>📊 Fleet Reports</b>\n"
        "  · Fault codes &amp; diagnostics\n"
        "  · Fuel levels &amp; consumption\n"
        "  · Vehicle health monitoring\n"
        "\n"
        "<b>🛠 Tools &amp; Tracking</b>\n"
        "  · Driver safety scorecards\n"
        "  · Live GPS location &amp; maps\n"
        "  · Route history &amp; geofences\n"
        "\n"
        "<b>💰 Cost Management</b>\n"
        "  · Fuel cost tracking\n"
        "  · Cost-per-mile analysis\n"
        "  · Maintenance records\n"
        "\n"
        "<b>🤖 AI Assistant</b>\n"
        "  · Ask anything about your fleet\n"
        "  · Instant insights &amp; summaries\n"
        "  · Smart follow-up suggestions\n"
        "\n"
        "<b>🔔 Alerts &amp; Auto Reports</b>\n"
        "  · Real-time fault alerts\n"
        "  · Scheduled PDF report delivery\n"
        "  · Geofence notifications\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  To get access, contact:\n"
        "  👉 https://t.me/Allen_Klein\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )


def format_register_success(account_name: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ✅  <b>ACCOUNT CREATED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  🏢  <b>{account_name}</b>\n"
        "  👑  You are the Owner\n"
        "\n"
        "  Next steps:\n"
        "  1️⃣  Tap 📡 <b>Add Company</b> in menu\n"
        "       to connect your Company\n"
        "\n"
        "  2️⃣  Tap ✉️ <b>Invite</b> in menu\n"
        "       to invite team members\n"
    )


def format_join_success(account_name: str, role_str: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  ✅  <b>JOINED {account_name.upper()}</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  Role: {role_str}\n"
        "\n"
        "  Tap a button to get started ▾\n"
    )


def format_account_info(account, companies, user) -> str:
    """Companies overview for the Companies sub-menu."""
    from permissions import role_display
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🏢  <b>{account.name} — COMPANIES</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  Your role: {role_display(user.role)}",
        "",
        f"  📡  <b>{len(companies)}</b> Samsara {'companies' if len(companies) != 1 else 'company'}",
    ]
    for co in companies:
        lines.append(f"     • {co.code} — {co.display_name or co.code}")

    return "\n".join(lines)


def format_invite_created(code: str, role_str: str, dept: str,
                         invite_link: str | None = None) -> str:
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  🔑  <b>INVITE CREATED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  Code:  <code>{code}</code>\n"
        f"  Role:  {role_str}\n"
        f"  Dept:  {dept}\n"
        "  Expires in 24 hours\n"
    )
    if invite_link:
        text += (
            "\n"
            "  📎 <b>Share this link:</b>\n"
            f"  {invite_link}\n"
            "\n"
            "  New member taps the link →\n"
            "  auto-joins your team instantly.\n"
        )
    else:
        text += (
            "\n"
            "  Share this with your\n"
            "  team member. They should:\n"
            "\n"
            "  1. Open this bot\n"
            "  2. Tap 🔑 Join\n"
            f"  3. Enter code: <code>{code}</code>\n"
        )
    return text


def format_users_list(users, account_name: str) -> str:
    """List all users for /users command."""
    from permissions import role_display
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  👥  <b>{account_name} — TEAM</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for u in users:
        alerts = "🔔" if u.alerts_on else "🔕"
        truck = f"  ·  Truck #{u.truck_num}" if u.truck_num else ""
        lines.append(
            f"  {role_display(u.role)}\n"
            f"     {u.linked_label}  ·  {u.department}{truck}\n"
            f"     {alerts}  Alerts {'ON' if u.alerts_on else 'OFF'}\n"
        )
    return "\n".join(lines)


def format_org_added(
    code: str,
    display_name: str,
    total_trucks: int | None = None,
    active_trucks: int | None = None,
) -> str:
    truck_info = ""
    if total_trucks is not None:
        truck_info += f"\n  🚛  Total trucks:  <b>{total_trucks}</b>"
        if active_trucks is not None:
            truck_info += (
                f"\n  ✅  Active (30 days):  <b>{active_trucks}</b>"
            )
    return (
        "━━━━━━━━━━━━━━━━━━━\n"
        "  ✅  <b>COMPANY CONNECTED</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  Code:  {code}\n"
        f"  Name:  {display_name}\n"
        f"{truck_info}\n"
    )


# ═══════════════════════════════════════════════════════════════════
#  System Owner — Admin Dashboard
# ═══════════════════════════════════════════════════════════════════

def format_system_owner_welcome() -> str:
    """Shown when system owner types /start — they're not a customer."""
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  ⚙️  <b>SYSTEM ADMIN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "  You are the platform owner.\n"
        "  Use the buttons below to\n"
        "  manage the platform.\n"
    )


def format_admin_dashboard(stats: dict) -> str:
    """System owner admin dashboard with bot-wide analytics."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        "  ⚙️  <b>ADMIN DASHBOARD</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  📊  <b>Platform Overview</b>",
        "",
        f"  🏢  Accounts:   <b>{stats['active_accounts']}</b> active"
        f"  ({stats['inactive_accounts']} disabled)",
        f"  👥  Users:      <b>{stats['active_users']}</b> active"
        f"  (of {stats['total_users']} total)",
        f"  📡  Samsara:    <b>{stats['active_companies']}</b> companies connected"
        f"  (of {stats['total_companies']} total)",
        f"  🔔  Alerts:     <b>{stats['alert_subscribers']}</b> subscribers",
        "",
    ]

    # Per-account breakdown
    details = stats.get("account_details", [])
    if details:
        lines.append("  ── ── ── ── ── ── ── ──")
        lines.append(f"  <b>Accounts ({len(details)})</b>")
        lines.append("")

        for d in details:
            acct = d["account"]
            users = d["users"]
            companies = d["companies"]
            from permissions import role_emoji
            user_str = ", ".join(
                f"{role_emoji(u.role)}{u.linked_label}"
                for u in users[:5]
            )
            if len(users) > 5:
                user_str += f" +{len(users) - 5}"

            co_str = ", ".join(o.code for o in companies) if companies else "none"

            lines.append(
                f"  🏢 <b>{acct.name}</b> (#{acct.id})\n"
                f"     {len(users)} users: {user_str}\n"
                f"     {len(companies)} companies: {co_str}\n"
                f"     Since: {acct.created_at[:10]}\n"
            )

    return "\n".join(lines)


def format_admin_account_detail(acct, companies, users) -> str:
    """Detailed view of a single account for system owner."""
    from permissions import role_display
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🏢  <b>{acct.name}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  ID:      #{acct.id}",
        f"  Slug:    {acct.slug}",
        f"  Active:  {'✅ Yes' if acct.is_active else '❌ No'}",
        f"  Since:   {acct.created_at[:10]}",
        "",
        f"  📡  <b>{len(companies)} {'Companies' if len(companies) != 1 else 'Company'}</b>",
    ]
    for co in companies:
        status = "🟢" if co.is_active else "🔴"
        lines.append(
            f"     {status} {co.code} — {co.display_name or co.code}\n"
            f"        Key: ...{co.samsara_api_key[-6:]}\n"
            f"        GPS filter: {co.active_days} days"
        )

    lines.append("")
    lines.append(f"  👥  <b>{len(users)} User{'s' if len(users) != 1 else ''}</b>")
    for u in users:
        alerts = "🔔" if u.alerts_on else "🔕"
        truck = f" · Truck #{u.truck_num}" if u.truck_num else ""
        active = "🟢" if u.is_active else "🔴"
        lines.append(
            f"     {active} {role_display(u.role)}\n"
            f"        {u.linked_label} · {u.department}{truck}\n"
            f"        {alerts} Alerts · Since: {u.created_at[:10]}"
        )

    return "\n".join(lines)


def format_admin_accounts_list(accounts) -> str:
    """Short list of all accounts for system owner."""
    if not accounts:
        return (
            "━━━━━━━━━━━━━━━━━━━\n"
            "  📋  <b>NO ACCOUNTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  No accounts registered yet."
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  📋  <b>ALL ACCOUNTS ({len(accounts)})</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for acct in accounts:
        status = "🟢" if acct.is_active else "🔴"
        lines.append(
            f"  {status} 🏢  <b>{acct.name}</b>  (#{acct.id})\n"
            f"      Since {acct.created_at[:10]}\n"
        )

    lines.append("  Tap account name for details.")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Efficiency — Merged Engine Hours + Driver Efficiency
# ═══════════════════════════════════════════════════════════════════

def _engine_bar(driving_pct: int | float) -> str:
    """Build a visual bar showing driving vs idle split."""
    filled = round(driving_pct / 10)
    bar = "▓" * filled + "░" * (10 - filled)
    return bar


def format_fleet_efficiency(
    vehicles: list[dict],
    days: int = 7,
    show_company: bool = False,
) -> list[str]:
    """Format merged efficiency data (engine hours + driver metrics).

    Each vehicle dict has engine-hours fields (always present) and
    optional driver fields (_driver_name, _fuel_gal, _mpg, etc.)
    which are None when no driver is assigned.
    """
    if not vehicles:
        return [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ℹ️  <b>NO EFFICIENCY DATA</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  No efficiency data found\n"
            f"  for the past {days} days."
        ]

    total_eng_s = sum(v.get("_engine_s", v["_engine_hours"] * 3600) for v in vehicles)
    total_drive_s = sum(v.get("_driving_s", v["_driving_hours"] * 3600) for v in vehicles)
    total_idle_s = sum(v.get("_idle_s", v["_idle_hours"] * 3600) for v in vehicles)
    total_eng = total_eng_s / 3600
    total_drive = total_drive_s / 3600
    total_idle = total_idle_s / 3600
    total_miles = sum(v.get("_miles", 0) for v in vehicles)
    avg_drive_pct = (total_drive_s / total_eng_s * 100) if total_eng_s > 0 else 0

    with_driver = [v for v in vehicles if v.get("_driver_name")]
    total_fuel = sum(v["_fuel_gal"] for v in with_driver if v.get("_fuel_gal"))
    fuel_miles = sum(v.get("_miles", 0) for v in with_driver)
    fleet_mpg = fuel_miles / total_fuel if total_fuel > 0 else 0

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  📊  <b>EFFICIENCY</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>{len(vehicles)}</b> trucks  ·  "
        f"👤 <b>{len(with_driver)}</b> drivers  ·  Past {days} days",
        f"  ⏱  <b>{total_eng:,.1f}h</b> engine  ·  "
        f"🚗 {total_drive:,.1f}h drive  ·  🅿️ {total_idle:,.1f}h idle",
        f"  🛣  <b>{total_miles:,}</b> mi  ·  "
        f"📈 {avg_drive_pct:.0f}% driving  ·  "
        f"⛽ {fleet_mpg:.1f} MPG",
        "",
        "  ── ── ── ── ── ── ── ──",
        "",
    ]

    for v in vehicles:
        name = v["name"]
        eng_h = v["_engine_hours"]
        drv_h = v["_driving_hours"]
        idle_h = v["_idle_hours"]
        drv_pct = v["_driving_pct"]
        idle_pct = v["_idle_pct"]
        tag = _company_tag(v, show_company)
        miles = v.get("_miles", 0)
        bar = _engine_bar(drv_pct)

        driver = v.get("_driver_name")
        if driver:
            mpg = v.get("_mpg", 0)
            eco = v.get("_green_pct", 0)
            ovr = v.get("_overspeed_min", 0)
            antic = v.get("_antic_brakes")
            total_brk = v.get("_total_brakes")
            brk_txt = f"🛑 {antic}/{total_brk}" if antic is not None else ""
            lines.append(
                f"  <b>{tag}#{name}</b>  ⏱ {eng_h}h · 🛣 {miles:,}mi\n"
                f"  {bar} 🚗 {drv_h}h ({drv_pct}%) · 🅿️ {idle_h}h ({idle_pct}%)\n"
                f"  👤 {driver}  ·  ⛽ {mpg}mpg  ·  "
                f"🌿 {eco}%  ·  ⚡ {ovr}m  {brk_txt}\n"
            )
        else:
            lines.append(
                f"  <b>{tag}#{name}</b>  ⏱ {eng_h}h · 🛣 {miles:,}mi\n"
                f"  {bar} 🚗 {drv_h}h ({drv_pct}%) · 🅿️ {idle_h}h ({idle_pct}%)\n"
            )

    now_et = datetime.now(_TZ_ET)
    lines.append(f"  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} EST")

    return _split_message("\n".join(lines))


# ═══════════════════════════════════════════════════════════════════
#  Vehicle Health Dashboard
# ═══════════════════════════════════════════════════════════════════

def _health_icon(alerts: list[str]) -> str:
    if not alerts:
        return "✅"
    if any(a in alerts for a in ("low_battery", "low_oil_pressure", "high_coolant_temp")):
        return "🔴"
    if any(a in alerts for a in ("low_def",)):
        return "🟡"
    return "⚠️"


def format_vehicle_health(
    vehicles: list[dict],
    show_company: bool = False,
) -> list[str]:
    """Format vehicle health diagnostics for Telegram."""
    if not vehicles:
        return [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ℹ️  <b>NO HEALTH DATA</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  No vehicle health data available."
        ]

    alert_count = sum(len(v.get("_health_alerts", [])) for v in vehicles)
    crit_count = sum(1 for v in vehicles if v.get("_health_alerts"))
    eng_on = sum(1 for v in vehicles if v.get("_health", {}).get("engine_on"))
    eng_off = len(vehicles) - eng_on

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  🏥  <b>VEHICLE HEALTH</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  📊  <b>{len(vehicles)}</b> trucks scanned",
        f"  🟢  <b>{eng_on}</b> engine ON  ·  ⚫ <b>{eng_off}</b> OFF",
        f"  ⚠️  <b>{crit_count}</b> with alerts  ·  <b>{alert_count}</b> total issues",
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

        detail = "  ·  ".join(parts) if parts else "No data"

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


# ═══════════════════════════════════════════════════════════════════
#  Fleet Weather (Ambient Conditions)
# ═══════════════════════════════════════════════════════════════════

def _temp_icon(temp_f: float | None) -> str:
    if temp_f is None:
        return "🌡"
    if temp_f <= 32:
        return "❄️"
    if temp_f >= 100:
        return "🔥"
    return "🌡"


def format_fleet_weather(
    vehicles: list[dict],
    show_company: bool = False,
) -> list[str]:
    """Format fleet weather conditions for Telegram."""
    if not vehicles:
        return [
            "━━━━━━━━━━━━━━━━━━━\n"
            "  ℹ️  <b>NO WEATHER DATA</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            "  No ambient temperature data available."
        ]

    temps = [v["_weather"]["temp_f"] for v in vehicles
             if v.get("_weather", {}).get("temp_f") is not None]
    freezing = sum(1 for t in temps if t <= 32)
    hot = sum(1 for t in temps if t >= 100)
    avg_temp = sum(temps) / len(temps) if temps else 0

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        "  🌡  <b>FLEET WEATHER</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  📊  <b>{len(vehicles)}</b> trucks reporting",
        f"  🌡  Avg temp: <b>{avg_temp:.0f}°F</b>",
    ]
    if freezing:
        lines.append(f"  ❄️  <b>{freezing}</b> below freezing (≤32°F)")
    if hot:
        lines.append(f"  🔥  <b>{hot}</b> extreme heat (≥100°F)")
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


# ═══════════════════════════════════════════════════════════════════
#  SAFETY EVENTS — Alert + Dashboard formatters
# ═══════════════════════════════════════════════════════════════════

_EVENT_EMOJI: dict[str, str] = {
    "crash": "💥",
    "braking": "🛑",
    "rollingStop": "↩️",
    "followingDistance": "🚗",
    "harshTurn": "🔄",
    "laneDeparture": "↔️",
    "acceleration": "🏎️",
    "other": "⚠️",
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


def format_event_alert(event: dict, show_company: bool = False) -> str:
    """Format a single safety event for an alert message (HTML)."""
    etype = event.get("event_type", "other")
    emoji = _EVENT_EMOJI.get(etype, "⚠️")
    ename = event.get("event_name", etype)
    vname = event.get("vehicle_name", "?")
    driver = event.get("driver_name", "Unassigned")
    g_force = event.get("g_force", 0)
    lat = event.get("latitude")
    lon = event.get("longitude")
    time_str = _fmt_time(event.get("time", ""))

    co = event.get("_org", "")
    co_label = f"[{co}] " if show_company and co else ""

    loc_str = "—"
    if lat is not None and lon is not None:
        loc_str = f"{lat:.4f}, {lon:.4f}"

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {emoji}  <b>{co_label}{ename}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  Truck #{vname}",
        f"  👤  {driver}",
        f"  ⚡  {g_force:.2f}g",
        f"  📍  {loc_str}",
        f"  🕐  {time_str}",
    ]
    return "\n".join(lines)


def format_events_dashboard(
    events: list[dict],
    days: int = 7,
    company_label: str | None = None,
) -> list[str]:
    """Format a summary dashboard of safety events (HTML, multi-message)."""
    if not events:
        return [f"✅ No events in the last {days} days."]

    label = company_label or "All Companies"

    # Count by type
    type_counts: dict[str, int] = {}
    driver_counts: dict[str, int] = {}
    g_mild = g_moderate = g_harsh = g_severe = 0

    for e in events:
        etype = e.get("event_type", "other")
        type_counts[etype] = type_counts.get(etype, 0) + 1

        dname = e.get("driver_name", "Unassigned")
        driver_counts[dname] = driver_counts.get(dname, 0) + 1

        g = e.get("g_force", 0)
        if g < 0.4:
            g_mild += 1
        elif g < 0.6:
            g_moderate += 1
        elif g < 0.8:
            g_harsh += 1
        else:
            g_severe += 1

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  🚨  <b>SAFETY EVENTS</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"  📅  Last {days} days  ·  {label}",
        f"  📊  Total: <b>{len(events)}</b> events",
        "",
    ]

    # By type
    for etype in ["crash", "braking", "rollingStop", "followingDistance",
                  "harshTurn", "laneDeparture", "acceleration", "other"]:
        count = type_counts.get(etype, 0)
        if count > 0:
            emoji = _EVENT_EMOJI.get(etype, "⚠️")
            lines.append(f"  {emoji}  {etype}: <b>{count}</b>")

    # Top drivers
    lines.append("")
    lines.append("  👤 <b>Top Drivers by Events</b>")
    sorted_drivers = sorted(driver_counts.items(), key=lambda x: -x[1])[:5]
    for name, count in sorted_drivers:
        lines.append(f"    {name}: {count} events")

    # G-force distribution
    lines.append("")
    lines.append("  ⚡ <b>G-Force Distribution</b>")
    lines.append(f"    Mild (&lt;0.4g): {g_mild}")
    lines.append(f"    Moderate (0.4-0.6g): {g_moderate}")
    lines.append(f"    Harsh (0.6-0.8g): {g_harsh}")
    lines.append(f"    Severe (&gt;0.8g): {g_severe}")

    now_et = datetime.now(_TZ_ET)
    lines.append(f"\n  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} EST")

    return _split_message("\n".join(lines))

