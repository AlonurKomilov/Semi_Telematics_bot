"""Inline keyboard builders — role-aware."""

from urllib.parse import quote

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, KeyboardButtonRequestChat,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)


from database import Role
from permissions import get_permissions, can_access_company_submenu
from samsara_client import COMPANY_DISPLAY


def main_menu_kb(role: Role, company_codes: list[str] | None = None) -> InlineKeyboardMarkup:
    """Build role-appropriate main menu.

    When the account has no connected Samsara companies (company_codes is
    empty), all fleet buttons are hidden and a prominent
    'Integrate Samsara API' button is shown instead.
    """
    perms = get_permissions(role)
    has_api = bool(company_codes)
    rows = []

    if has_api:
        # ── Fleet buttons (only when API is connected) ──────────
        row1 = []
        row2 = []

        if perms.can_faults:
            row1.append(InlineKeyboardButton("🔧 Faults", callback_data="cmd_faults"))
        if perms.can_fuel:
            row2.append(InlineKeyboardButton("⛽ Fuel", callback_data="cmd_fuel"))
        if perms.can_alerts_all or perms.can_alerts_own:
            row2.append(InlineKeyboardButton("🔔 Alerts", callback_data="cmd_alerts"))

        if row1:
            rows.append(row1)
        if row2:
            rows.append(row2)

        # New fleet reports — accessible to fleet_manager+ (same as faults)
        if perms.can_faults:
            rows.append([
                InlineKeyboardButton("🏥 Health", callback_data="cmd_health"),
                InlineKeyboardButton("📊 Efficiency", callback_data="cmd_efficiency"),
            ])
            rows.append([
                InlineKeyboardButton("🌡 Weather", callback_data="cmd_weather"),
            ])

        # Truck lookup for non-drivers with truck_all access
        if perms.can_truck_all:
            rows.append([InlineKeyboardButton("🚛 Search Truck", callback_data="cmd_truck_prompt")])

        # Per-company buttons (only when >1 company and role can filter)
        if len(company_codes) > 1 and can_access_company_submenu(role):
            company_row = [
                InlineKeyboardButton(code, callback_data=f"co_{code}")
                for code in company_codes
            ]
            rows.append(company_row)

        # Driver: show truck button
        if role == Role.DRIVER:
            rows.append([InlineKeyboardButton("🚛 My Truck", callback_data="cmd_mytruck")])
    else:
        # ── No API connected — show integration prompt ──────────
        if perms.can_manage_companies:
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
    if perms.can_manage_companies and has_api:
        mgmt2.append(InlineKeyboardButton("📡 Add Company", callback_data="cmd_addcompany_prompt"))
    if mgmt2:
        rows.append(mgmt2)

    # API Status — owner & admin (only when API is connected)
    if has_api and perms.can_manage_account:
        rows.append([InlineKeyboardButton("📡 API Status", callback_data="cmd_api_status")])

    # Group / channel management — owner & admin
    if perms.can_manage_users:
        rows.append([InlineKeyboardButton("💬 Groups", callback_data="cmd_groups")])

    return InlineKeyboardMarkup(rows)


def faults_menu_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Shown after the faults PDF — drill into Critical or go back."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚨 Critical Only (PDF)", callback_data=f"cmd_critical{suffix}")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def co_menu_kb(company: str) -> InlineKeyboardMarkup:
    """Sub-menu for a specific company."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔧 {company} Faults", callback_data=f"cofaults_{company}"),
        ],
        [
            InlineKeyboardButton(f"⛽ {company} Fuel", callback_data=f"cofuel_{company}"),
            InlineKeyboardButton(f"📊 {company} Efficiency", callback_data=f"coeff_{company}"),
        ],
        [
            InlineKeyboardButton(f"🏥 {company} Health", callback_data=f"cohealth_{company}"),
            InlineKeyboardButton(f"🌡 {company} Weather", callback_data=f"coweather_{company}"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def efficiency_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Ask the user to pick PDF or CSV for the Efficiency report."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"eff_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"eff_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def fuel_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Ask the user to pick PDF or CSV for the Fuel & DEF report."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"fuel_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"fuel_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def health_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Ask the user to pick PDF or CSV for the Vehicle Health report."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"health_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"health_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def faults_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Ask the user to pick PDF or CSV for the Fault report."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"faults_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"faults_csv{suffix}"),
        ],
        [InlineKeyboardButton("🚨 Critical Only", callback_data=f"cmd_critical{suffix}")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def group_picker_kb() -> ReplyKeyboardMarkup:
    """Reply keyboard with native Telegram group/channel picker buttons."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(
                "👥 Pick a Group",
                request_chat=KeyboardButtonRequestChat(
                    request_id=1,
                    chat_is_channel=False,
                    request_title=True,
                    request_username=True,
                ),
            )],
            [KeyboardButton(
                "📢 Pick a Channel",
                request_chat=KeyboardButtonRequestChat(
                    request_id=2,
                    chat_is_channel=True,
                    request_title=True,
                    request_username=True,
                ),
            )],
            [KeyboardButton("❌ Cancel")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def skip_name_kb() -> InlineKeyboardMarkup:
    """Step 3/3 of Add Company wizard — skip or go back."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip", callback_data="addcompany_skip_name")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
    ])


def truck_kb(
    truck_name: str | None = None,
    company: str | None = None,
    show_faults: bool = True,
) -> InlineKeyboardMarkup:
    """Buttons shown below a single-truck detail.

    *show_faults* — when False (Dispatcher / Driver) the PDF report
    button is hidden so role restrictions are enforced visually as
    well as logically.
    """
    rows = []
    if show_faults and truck_name and company:
        rows.append([
            InlineKeyboardButton("📄 PDF Report", callback_data=f"truckfaults_{company}_{truck_name}"),
        ])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def truck_picker_kb(matches: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for v in matches:
        co = v.get("_org", "?")
        name = v["name"]
        label = f"#{name} — {COMPANY_DISPLAY.get(co, co)} ({co})"
        rows.append([InlineKeyboardButton(label, callback_data=f"cotruck_{co}_{name}")])
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


def truck_company_picker_kb(company_codes: list[str]) -> InlineKeyboardMarkup:
    """Let user pick a company before browsing trucks, or show all."""
    rows = []
    if len(company_codes) > 1:
        rows.append([InlineKeyboardButton(
            "🚛 All Companies", callback_data="trucks_browse_ALL",
        )])
    for code in company_codes:
        display = COMPANY_DISPLAY.get(code, code)
        rows.append([InlineKeyboardButton(
            f"🚛 {display} ({code})", callback_data=f"trucks_browse_{code}",
        )])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def vehicle_list_kb(
    vehicles: list[dict],
    page: int = 0,
    page_size: int = 8,
    company_filter: str | None = None,
) -> InlineKeyboardMarkup:
    """Paginated truck list — each truck is a tappable button."""
    start = page * page_size
    page_vehicles = vehicles[start:start + page_size]
    total_pages = max(1, (len(vehicles) + page_size - 1) // page_size)

    rows = []
    for v in page_vehicles:
        name = v["name"]
        co = v.get("_org", "")
        label = f"#{name}"
        if co:
            label += f" — {co}"
        rows.append([InlineKeyboardButton(label, callback_data=f"cotruck_{co}_{name}")])

    # Pagination
    nav = []
    prefix = f"trucks_page_{company_filter or 'ALL'}"
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{prefix}_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"{prefix}_{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)
