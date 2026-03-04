"""
Telegram message formatters — clean, rich, easy-to-read fleet reports.
Uses HTML parse mode.  Multi-org aware.  Role-aware help menus.
"""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

_TZ_ET = ZoneInfo("America/New_York")

from samsara_client import ORG_DISPLAY


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


def _org_tag(v: dict, show_org: bool) -> str:
    """Return '[PTG] ' prefix when multi-org context."""
    if not show_org:
        return ""
    org = v.get("_org", "")
    return f"[{org}] " if org else ""


# ═══════════════════════════════════════════════════════════════════
#  /start  and  /help
# ═══════════════════════════════════════════════════════════════════

def format_help(org_codes: list[str] | None = None,
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

    org_line = ""
    has_api = bool(org_codes)
    if org_codes and len(org_codes) > 1:
        names = [f"{c} ({ORG_DISPLAY.get(c, c)})" for c in org_codes]
        org_line = (
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
        "╔══════════════════════════╗\n"
        "     🚛  <b>Semi Telematics</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        "Real-time fleet monitoring\n"
        "powered by Samsara\n"
        f"{role_line}{acct_line}{org_line}"
        "\n"
        f"{status_line}"
        "\n"
        "▸ <b>Tap a button below to begin</b> ▾\n"
    )


# ═══════════════════════════════════════════════════════════════════
#  /truck <number>  —  Single Truck Detail
# ═══════════════════════════════════════════════════════════════════

def format_truck_detail(v: dict, show_org: bool = False,
                       show_faults: bool = True) -> list[str]:
    fc = v.get("fault_codes", {})
    j1939 = fc.get("j1939", {})
    dtcs = j1939.get("diagnosticTroubleCodes", [])
    lights = j1939.get("checkEngineLights", {})
    loc = v.get("location", {})
    fuel = v.get("fuel", {})
    fuel_pct = fuel.get("value")
    org = v.get("_org", "")

    org_label = ""
    if show_org and org:
        org_label = f"\n  🏢  {ORG_DISPLAY.get(org, org)}  ({org})"

    lines = [
        "┌─────────────────────────┐",
        f"  🚛  <b>TRUCK  #{v['name']}</b>",
        "└─────────────────────────┘",
        f"{org_label}",
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
#  Truck Picker — when name matches multiple orgs
# ═══════════════════════════════════════════════════════════════════

def format_truck_picker(truck_name: str, matches: list[dict]) -> str:
    """Show disambiguation when a truck name exists in multiple orgs."""
    lines = [
        "┌─────────────────────────┐",
        f"  🔍  <b>#{truck_name}</b>  found in",
        f"       {len(matches)} companies",
        "└─────────────────────────┘",
        "",
        "  Select which one:",
        "",
    ]
    for v in matches:
        org = v.get("_org", "?")
        name = ORG_DISPLAY.get(org, org)
        lines.append(f"  • <b>{org}</b> — {name}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  /fuel  —  Low Fuel Alert
# ═══════════════════════════════════════════════════════════════════

def format_low_fuel(low_fuel_vehicles: list, threshold: int,
                    show_org: bool = False) -> str:
    if not low_fuel_vehicles:
        return (
            "┌─────────────────────────┐\n"
            "  ✅  <b>FUEL OK</b>\n"
            "└─────────────────────────┘\n"
            f"\n  All trucks above {threshold}% fuel."
        )

    lines = [
        "┌─────────────────────────┐",
        "  ⛽  <b>LOW FUEL ALERT</b>",
        "└─────────────────────────┘",
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
        tag = _org_tag(v, show_org)

        lines.append(
            f"  <b>{tag}#{name}</b>  ·  📍 {city}\n"
            f"  {bar}\n"
        )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Auto-Alert  —  New Fault Notification
# ═══════════════════════════════════════════════════════════════════

def format_new_fault_alert(vehicle: dict, new_dtcs: list,
                           show_org: bool = False) -> str:
    name = vehicle.get("name", "?")
    loc = vehicle.get("location", {})
    city = _short_location(loc)
    org = vehicle.get("_org", "")

    org_label = ""
    if show_org and org:
        org_label = f"\n  🏢  {ORG_DISPLAY.get(org, org)}  ({org})"

    lines = [
        "╔══════════════════════════╗",
        "  🚨  <b>NEW FAULT DETECTED</b>",
        "╚══════════════════════════╝",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"  📍  {city}",
        f"{org_label}",
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


# ═══════════════════════════════════════════════════════════════════
#  Registration / Account Management Formatters
# ═══════════════════════════════════════════════════════════════════

def format_welcome_unregistered(support_contact: str = "") -> str:
    """Shown to users who haven't registered or joined yet."""
    support_line = ""
    if support_contact:
        support_line = (
            "\n"
            f"  💬 Questions? Contact\n"
            f"     {support_contact}\n"
        )
    return (
        "╔══════════════════════════╗\n"
        "     🚛  <b>Semi Telematics</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        "  Welcome! You're not\n"
        "  registered yet.\n"
        "\n"
        "  <b>New company?</b>\n"
        "  Tap 📝 Register below\n"
        "\n"
        "  <b>Joining a team?</b>\n"
        "  Tap 🔑 Join below\n"
        "  (get the code from your admin)\n"
        f"{support_line}"
    )


def format_register_success(account_name: str) -> str:
    return (
        "┌─────────────────────────┐\n"
        "  ✅  <b>ACCOUNT CREATED</b>\n"
        "└─────────────────────────┘\n"
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
        "┌─────────────────────────┐\n"
        f"  ✅  <b>JOINED {account_name.upper()}</b>\n"
        "└─────────────────────────┘\n"
        "\n"
        f"  Role: {role_str}\n"
        "\n"
        "  Tap a button to get started ▾\n"
    )


def format_account_info(account, orgs, users, user) -> str:
    """Account overview for /account command."""
    from permissions import role_display
    lines = [
        "┌─────────────────────────┐",
        f"  🏢  <b>{account.name}</b>",
        "└─────────────────────────┘",
        "",
        f"  Your role: {role_display(user.role)}",
        "",
        f"  📡  <b>{len(orgs)}</b> Samsara {'companies' if len(orgs) != 1 else 'company'}",
    ]
    for org in orgs:
        lines.append(f"     • {org.code} — {org.display_name or org.code}")

    lines.append("")
    lines.append(f"  👥  <b>{len(users)}</b> team member{'s' if len(users) != 1 else ''}")
    for u in users:
        from permissions import role_emoji
        emoji = role_emoji(u.role)
        truck = f" (truck #{u.truck_num})" if u.truck_num else ""
        lines.append(f"     {emoji}  ID {u.telegram_id} — {u.role.value}{truck}")

    return "\n".join(lines)


def format_invite_created(code: str, role_str: str, dept: str,
                         invite_link: str | None = None) -> str:
    text = (
        "┌─────────────────────────┐\n"
        "  🔑  <b>INVITE CREATED</b>\n"
        "└─────────────────────────┘\n"
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
        "┌─────────────────────────┐",
        f"  👥  <b>{account_name} — TEAM</b>",
        "└─────────────────────────┘",
        "",
    ]
    for u in users:
        alerts = "🔔" if u.alerts_on else "🔕"
        truck = f"  ·  Truck #{u.truck_num}" if u.truck_num else ""
        lines.append(
            f"  {role_display(u.role)}\n"
            f"     TG: {u.telegram_id}  ·  {u.department}{truck}\n"
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
        "┌─────────────────────────┐\n"
        "  ✅  <b>COMPANY CONNECTED</b>\n"
        "└─────────────────────────┘\n"
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
        "╔══════════════════════════╗\n"
        "     ⚙️  <b>SYSTEM ADMIN</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        "  You are the platform owner.\n"
        "  Use the buttons below to\n"
        "  manage the platform.\n"
    )


def format_admin_dashboard(stats: dict) -> str:
    """System owner admin dashboard with bot-wide analytics."""
    lines = [
        "╔══════════════════════════╗",
        "     ⚙️  <b>ADMIN DASHBOARD</b>",
        "╚══════════════════════════╝",
        "",
        f"  📊  <b>Platform Overview</b>",
        "",
        f"  🏢  Accounts:   <b>{stats['active_accounts']}</b> active"
        f"  ({stats['inactive_accounts']} disabled)",
        f"  👥  Users:      <b>{stats['active_users']}</b> active"
        f"  (of {stats['total_users']} total)",
        f"  📡  Samsara:    <b>{stats['active_orgs']}</b> orgs connected"
        f"  (of {stats['total_orgs']} total)",
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
            orgs = d["orgs"]
            from permissions import role_emoji
            user_str = ", ".join(
                f"{role_emoji(u.role)}{u.telegram_id}"
                for u in users[:5]
            )
            if len(users) > 5:
                user_str += f" +{len(users) - 5}"

            org_str = ", ".join(o.code for o in orgs) if orgs else "none"

            lines.append(
                f"  🏢 <b>{acct.name}</b> (#{acct.id})\n"
                f"     {len(users)} users: {user_str}\n"
                f"     {len(orgs)} orgs: {org_str}\n"
                f"     Since: {acct.created_at[:10]}\n"
            )

    return "\n".join(lines)


def format_admin_account_detail(acct, orgs, users) -> str:
    """Detailed view of a single account for system owner."""
    from permissions import role_display
    lines = [
        "┌─────────────────────────┐",
        f"  🏢  <b>{acct.name}</b>",
        "└─────────────────────────┘",
        "",
        f"  ID:      #{acct.id}",
        f"  Slug:    {acct.slug}",
        f"  Active:  {'✅ Yes' if acct.is_active else '❌ No'}",
        f"  Since:   {acct.created_at[:10]}",
        "",
        f"  📡  <b>{len(orgs)} {'Companies' if len(orgs) != 1 else 'Company'}</b>",
    ]
    for org in orgs:
        status = "🟢" if org.is_active else "🔴"
        lines.append(
            f"     {status} {org.code} — {org.display_name or org.code}\n"
            f"        Key: ...{org.samsara_api_key[-6:]}\n"
            f"        GPS filter: {org.active_days} days"
        )

    lines.append("")
    lines.append(f"  👥  <b>{len(users)} User{'s' if len(users) != 1 else ''}</b>")
    for u in users:
        alerts = "🔔" if u.alerts_on else "🔕"
        truck = f" · Truck #{u.truck_num}" if u.truck_num else ""
        active = "🟢" if u.is_active else "🔴"
        lines.append(
            f"     {active} {role_display(u.role)}\n"
            f"        TG: {u.telegram_id} · {u.department}{truck}\n"
            f"        {alerts} Alerts · Since: {u.created_at[:10]}"
        )

    return "\n".join(lines)


def format_admin_accounts_list(accounts) -> str:
    """Short list of all accounts for system owner."""
    if not accounts:
        return (
            "┌─────────────────────────┐\n"
            "  📋  <b>NO ACCOUNTS</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            "  No accounts registered yet."
        )

    lines = [
        "┌─────────────────────────┐",
        f"  📋  <b>ALL ACCOUNTS ({len(accounts)})</b>",
        "└─────────────────────────┘",
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
#  Engine Hours — Weekly Breakdown
# ═══════════════════════════════════════════════════════════════════

def _engine_bar(driving_pct: int | float) -> str:
    """Build a visual bar showing driving vs idle split."""
    filled = round(driving_pct / 10)
    bar = "▓" * filled + "░" * (10 - filled)
    return bar


def format_engine_hours(
    vehicles: list[dict],
    days: int = 7,
    show_org: bool = False,
) -> list[str]:
    """Format engine hours breakdown for Telegram.

    Each vehicle dict is expected to have:
        name, _engine_hours, _driving_pct, _idle_pct,
        _driving_hours, _idle_hours, _org (optional)
    """
    if not vehicles:
        return [
            "┌─────────────────────────┐\n"
            "  ℹ️  <b>NO ENGINE DATA</b>\n"
            "└─────────────────────────┘\n"
            "\n"
            f"  No engine hour data found\n"
            f"  for the past {days} days."
        ]

    total_eng = sum(v["_engine_hours"] for v in vehicles)
    total_drive = sum(v["_driving_hours"] for v in vehicles)
    total_idle = sum(v["_idle_hours"] for v in vehicles)
    total_miles = sum(v.get("_miles", 0) for v in vehicles)
    avg_drive_pct = (total_drive / total_eng * 100) if total_eng > 0 else 0

    lines = [
        "┌─────────────────────────┐",
        "  ⏱  <b>ENGINE HOURS</b>",
        "└─────────────────────────┘",
        "",
        f"  📊  <b>{len(vehicles)}</b> trucks  ·  Past {days} days",
        f"  ⏱  <b>{total_eng:,.1f}h</b> total engine time",
        f"  🛣  <b>{total_miles:,}</b> miles driven",
        f"  🚗 {total_drive:,.1f}h driving  ·  🅿️ {total_idle:,.1f}h idle",
        f"  📈 Fleet avg: {avg_drive_pct:.0f}% driving",
        "",
        "  ── ── ── ── ── ── ── ──",
        "",
    ]

    for v in vehicles:
        name = v["name"]
        eng_h = v["_engine_hours"]
        drv_pct = v["_driving_pct"]
        idle_pct = v["_idle_pct"]
        drv_h = v["_driving_hours"]
        idle_h = v["_idle_hours"]
        tag = _org_tag(v, show_org)

        miles = v.get("_miles", 0)
        bar = _engine_bar(drv_pct)
        lines.append(
            f"  <b>{tag}#{name}</b>  ⏱ {eng_h}h · 🛣 {miles:,}mi\n"
            f"  {bar} {drv_pct}% 🚗 · {idle_pct}% 🅿️\n"
        )

    now_et = datetime.now(_TZ_ET)
    lines.append(f"  🕐  {now_et.strftime('%b %d, %Y  %I:%M %p')} EST")

    return _split_message("\n".join(lines))

