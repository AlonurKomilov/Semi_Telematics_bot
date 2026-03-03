"""
Telegram message formatters — clean, rich, easy-to-read fleet reports.
Uses HTML parse mode.  Multi-org aware.  Role-aware help menus.
"""

from datetime import datetime, timezone
from typing import Optional

from samsara_client import ORG_DISPLAY


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _fmt_time(iso_str: str) -> str:
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y  %I:%M %p")
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
    if org_codes and len(org_codes) > 1:
        names = [f"{c} ({ORG_DISPLAY.get(c, c)})" for c in org_codes]
        org_line = (
            "\n"
            "  🏢 Companies:\n"
            "  " + "  ·  ".join(names) + "\n"
        )

    # Build command list based on role
    cmds = ""
    if user:
        from permissions import get_permissions
        p = get_permissions(user.role)
        lines = []
        if p.can_faults:
            lines.append("  /faults    — Fault report (PDF)")
        if p.can_critical:
            lines.append("  /critical  — Critical faults (PDF)")
        if p.can_truck_all:
            lines.append("  /truck 134 — Single truck detail")
        elif p.can_truck_own and user.truck_num:
            lines.append(f"  /truck     — Your truck #{user.truck_num}")
        if p.can_fuel:
            lines.append("  /fuel      — Low fuel alert")
        if p.can_alerts_all or p.can_alerts_own:
            lines.append("  /alerts    — Auto-notify")
        if p.can_invite:
            lines.append("  /invite    — Invite team member")
        if p.can_manage_users:
            lines.append("  /users     — Manage users")
        if p.can_manage_orgs:
            lines.append("  /addorg    — Connect Samsara org")
        if p.can_manage_account:
            lines.append("  /account   — Account settings")
        cmds = "\n".join(lines) + "\n"
    else:
        cmds = (
            "  /faults    — Fault report (PDF)\n"
            "  /critical  — Critical faults (PDF)\n"
            "  /truck 134 — Single truck detail\n"
            "  /fuel      — Low fuel alert\n"
            "  /alerts    — Auto-notify\n"
        )

    return (
        "╔══════════════════════════╗\n"
        "     🚛  <b>Semi Telematics</b>\n"
        "╚══════════════════════════╝\n"
        "\n"
        "Real-time fleet monitoring\n"
        "powered by Samsara\n"
        f"{role_line}{acct_line}{org_line}"
        "\n"
        "▸ <b>Use the buttons below</b>\n"
        "  or type any command:\n"
        "\n"
        f"{cmds}"
    )


# ═══════════════════════════════════════════════════════════════════
#  /truck <number>  —  Single Truck Detail
# ═══════════════════════════════════════════════════════════════════

def format_truck_detail(v: dict, show_org: bool = False) -> list[str]:
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
        "",
        f"  💡 Dash:  {_light_badges(lights)}",
    ]

    lines.append("")
    if not dtcs:
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
    if fault_time:
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

    lines.append(f"  ▸  /truck {name}  for full details")
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
        "  /register Your Company Name\n"
        "\n"
        "  <b>Joining a team?</b>\n"
        "  /join XXXX-XXXX\n"
        "  (use the code from your admin)\n"
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
        "  1️⃣  /addorg CODE:api_key\n"
        "       Connect your Samsara org\n"
        "\n"
        "  2️⃣  /invite fleet_manager\n"
        "       Invite team members\n"
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
        f"  Plan:   {account.tier.upper()}",
        f"  Your role: {role_display(user.role)}",
        "",
        f"  📡  <b>{len(orgs)}</b> Samsara organization{'s' if len(orgs) != 1 else ''}",
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


def format_invite_created(code: str, role_str: str, dept: str) -> str:
    return (
        "┌─────────────────────────┐\n"
        "  🔑  <b>INVITE CREATED</b>\n"
        "└─────────────────────────┘\n"
        "\n"
        f"  Code:  <code>{code}</code>\n"
        f"  Role:  {role_str}\n"
        f"  Dept:  {dept}\n"
        "  Expires in 24 hours\n"
        "\n"
        "  Share this with your\n"
        "  team member:\n"
        "\n"
        f"  👉  <code>/join {code}</code>\n"
    )


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


def format_org_added(code: str, display_name: str, truck_count: int | None = None) -> str:
    truck_info = f"\n  Found {truck_count} active trucks" if truck_count is not None else ""
    return (
        "┌─────────────────────────┐\n"
        "  ✅  <b>ORG CONNECTED</b>\n"
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
        "  Use /admin to see analytics.\n"
        "\n"
        "  <b>Admin commands:</b>\n"
        "  /admin       — Dashboard\n"
        "  /accounts    — All accounts\n"
        "  /sysaccount  — Account detail\n"
        "  /broadcast   — Message all\n"
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
            tier_badge = {"free": "🆓", "pro": "⭐", "enterprise": "🏆"}.get(
                acct.tier, "📋"
            )

            from permissions import role_emoji
            user_str = ", ".join(
                f"{role_emoji(u.role)}{u.telegram_id}"
                for u in users[:5]
            )
            if len(users) > 5:
                user_str += f" +{len(users) - 5}"

            org_str = ", ".join(o.code for o in orgs) if orgs else "none"

            lines.append(
                f"  {tier_badge} <b>{acct.name}</b> (#{acct.id})\n"
                f"     {len(users)} users: {user_str}\n"
                f"     {len(orgs)} orgs: {org_str}\n"
                f"     Tier: {acct.tier.upper()} · "
                f"Since: {acct.created_at[:10]}\n"
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
        f"  Tier:    {acct.tier.upper()}",
        f"  Active:  {'✅ Yes' if acct.is_active else '❌ No'}",
        f"  Since:   {acct.created_at[:10]}",
        "",
        f"  📡  <b>{len(orgs)} Organization{'s' if len(orgs) != 1 else ''}</b>",
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
        tier_badge = {"free": "🆓", "pro": "⭐", "enterprise": "🏆"}.get(
            acct.tier, "📋"
        )
        lines.append(
            f"  {status} {tier_badge}  <b>{acct.name}</b>  (#{acct.id})\n"
            f"      {acct.tier.upper()} · Since {acct.created_at[:10]}\n"
        )

    lines.append("  Use /sysaccount <b>ID</b> for details")

    return "\n".join(lines)

