"""Inline keyboard builders — role-aware."""

from urllib.parse import quote

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database import Role
from permissions import get_permissions, can_access_org_submenu
from samsara_client import ORG_DISPLAY


def main_menu_kb(role: Role, org_codes: list[str] | None = None) -> InlineKeyboardMarkup:
    """Build role-appropriate main menu.

    When the account has no connected Samsara companies (org_codes is
    empty), all fleet buttons are hidden and a prominent
    'Integrate Samsara API' button is shown instead.
    """
    perms = get_permissions(role)
    has_api = bool(org_codes)
    rows = []

    if has_api:
        # ── Fleet buttons (only when API is connected) ──────────
        row1 = []
        row2 = []

        if perms.can_faults:
            row1.append(InlineKeyboardButton("🔧 Faults (PDF)", callback_data="cmd_faults"))
        if perms.can_critical:
            row1.append(InlineKeyboardButton("🚨 Critical (PDF)", callback_data="cmd_critical"))
        if perms.can_fuel:
            row2.append(InlineKeyboardButton("⛽ Fuel", callback_data="cmd_fuel"))
        if perms.can_alerts_all or perms.can_alerts_own:
            row2.append(InlineKeyboardButton("🔔 Alerts", callback_data="cmd_alerts"))

        if row1:
            rows.append(row1)
        if row2:
            rows.append(row2)

        # Engine Hours — accessible to fleet_manager+ (same as faults)
        if perms.can_faults:
            rows.append([InlineKeyboardButton("⏱ Engine Hours (PDF)", callback_data="cmd_engine_hours")])

        # Truck lookup for non-drivers with truck_all access
        if perms.can_truck_all:
            rows.append([InlineKeyboardButton("🚛 Search Truck", callback_data="cmd_truck_prompt")])

        # Per-org buttons (only when >1 org and role can filter)
        if len(org_codes) > 1 and can_access_org_submenu(role):
            org_row = [
                InlineKeyboardButton(code, callback_data=f"org_{code}")
                for code in org_codes
            ]
            rows.append(org_row)

        # Driver: show truck button
        if role == Role.DRIVER:
            rows.append([InlineKeyboardButton("🚛 My Truck", callback_data="cmd_mytruck")])
    else:
        # ── No API connected — show integration prompt ──────────
        if perms.can_manage_orgs:
            rows.append([InlineKeyboardButton(
                "📡 Integrate Samsara API",
                callback_data="cmd_integrate_guide",
            )])
        else:
            rows.append([InlineKeyboardButton(
                "ℹ️ Waiting for API Setup",
                callback_data="cmd_no_api_info",
            )])

    # ── Management buttons (always visible) ─────────────────────
    mgmt = []
    if perms.can_manage_account:
        mgmt.append(InlineKeyboardButton("⚙️ Account", callback_data="cmd_account"))
    if perms.can_manage_users:
        mgmt.append(InlineKeyboardButton("👥 Team", callback_data="cmd_users"))
    if mgmt:
        rows.append(mgmt)

    # Invite & Add Company — always visible for eligible roles
    mgmt2 = []
    if perms.can_invite:
        mgmt2.append(InlineKeyboardButton("✉️ Invite", callback_data="cmd_invite_pick"))
    if perms.can_manage_orgs and has_api:
        mgmt2.append(InlineKeyboardButton("📡 Add Company", callback_data="cmd_addorg_prompt"))
    if mgmt2:
        rows.append(mgmt2)

    return InlineKeyboardMarkup(rows)


def org_menu_kb(org: str) -> InlineKeyboardMarkup:
    """Sub-menu for a specific org."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔧 {org} Faults", callback_data=f"orgfaults_{org}"),
            InlineKeyboardButton(f"🚨 {org} Critical", callback_data=f"orgcritical_{org}"),
        ],
        [
            InlineKeyboardButton(f"⛽ {org} Fuel", callback_data=f"orgfuel_{org}"),
            InlineKeyboardButton(f"⏱ {org} Engine Hrs", callback_data=f"orgenghrs_{org}"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def skip_name_kb() -> InlineKeyboardMarkup:
    """Step 3/3 of Add Company wizard — skip or go back."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="addorg_skip_name")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def truck_kb(
    truck_name: str | None = None,
    org: str | None = None,
    show_faults: bool = True,
) -> InlineKeyboardMarkup:
    """Buttons shown below a single-truck detail.

    *show_faults* — when False (Dispatcher / Driver) the fault &
    critical buttons are hidden so role restrictions are enforced
    visually as well as logically.
    """
    rows = []
    if show_faults:
        if truck_name and org:
            faults_cb = f"truckfaults_{org}_{truck_name}"
            crit_cb   = f"truckcritical_{org}_{truck_name}"
        else:
            faults_cb = "cmd_faults"
            crit_cb   = "cmd_critical"
        rows.append([
            InlineKeyboardButton("🔧 All Faults", callback_data=faults_cb),
            InlineKeyboardButton("🚨 Critical", callback_data=crit_cb),
        ])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def truck_picker_kb(matches: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for v in matches:
        org = v.get("_org", "?")
        name = v["name"]
        label = f"#{name} — {ORG_DISPLAY.get(org, org)} ({org})"
        rows.append([InlineKeyboardButton(label, callback_data=f"orgtruck_{org}_{name}")])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def unregistered_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Register Company", callback_data="cmd_register_help")],
        [InlineKeyboardButton("🔑 Join with Code", callback_data="cmd_join_help")],
    ])


def system_owner_kb() -> InlineKeyboardMarkup:
    """System owner admin panel keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Dashboard", callback_data="sys_dashboard"),
            InlineKeyboardButton("📋 Accounts", callback_data="sys_accounts"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="sys_dashboard"),
        ],
    ])


def invite_kb(invite_link: str | None = None) -> InlineKeyboardMarkup:
    """Keyboard shown after an invite is created.

    If *invite_link* is provided, a "Send to Team Member" button
    opens Telegram's native share picker so the user can forward the
    invite directly to another person.
    """
    rows = []
    if invite_link:
        share_text = (
            "\U0001f69b You're invited to join our team!\n"
            "\n"
            "Tap the link below to join instantly \u2014\n"
            "no invite code needed:\n"
        )
        share_url = (
            f"https://t.me/share/url"
            f"?text={quote(share_text)}"
            f"&url={quote(invite_link)}"
        )
        rows.append([InlineKeyboardButton("📤 Send to Team Member", url=share_url)])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def truck_company_picker_kb(org_codes: list[str]) -> InlineKeyboardMarkup:
    """Let user pick a company before browsing trucks, or show all."""
    rows = []
    if len(org_codes) > 1:
        rows.append([InlineKeyboardButton(
            "🚛 All Companies", callback_data="trucks_browse_ALL",
        )])
    for code in org_codes:
        display = ORG_DISPLAY.get(code, code)
        rows.append([InlineKeyboardButton(
            f"🚛 {display} ({code})", callback_data=f"trucks_browse_{code}",
        )])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def vehicle_list_kb(
    vehicles: list[dict],
    page: int = 0,
    page_size: int = 8,
    org_filter: str | None = None,
) -> InlineKeyboardMarkup:
    """Paginated truck list — each truck is a tappable button."""
    start = page * page_size
    page_vehicles = vehicles[start:start + page_size]
    total_pages = max(1, (len(vehicles) + page_size - 1) // page_size)

    rows = []
    for v in page_vehicles:
        name = v["name"]
        org = v.get("_org", "")
        label = f"#{name}"
        if org:
            label += f" — {org}"
        rows.append([InlineKeyboardButton(label, callback_data=f"orgtruck_{org}_{name}")])

    # Pagination
    nav = []
    prefix = f"trucks_page_{org_filter or 'ALL'}"
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{prefix}_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"{prefix}_{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)
