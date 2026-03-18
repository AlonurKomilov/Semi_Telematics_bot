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

    Grouped into sub-menus to keep the top-level clean (max ~6 buttons).
    When the account has no connected Samsara companies (company_codes is
    empty), all fleet buttons are hidden and a prominent
    'Integrate Samsara API' button is shown instead.
    """
    perms = get_permissions(role)
    has_api = bool(company_codes)
    rows = []

    if has_api:
        # ── Grouped sub-menus (only when API is connected) ──────
        has_reports = (perms.can_faults or perms.can_fuel
                       or perms.can_truck_all or perms.can_truck_own)
        has_tools = (perms.can_scorecard_all or perms.can_scorecard_own
                     or perms.can_location_map or perms.can_location_own
                     or perms.can_route_all or perms.can_route_own
                     or perms.can_geofence_all or perms.can_geofence_own)
        has_costs = (perms.can_fuel_cost or perms.can_cost_per_mile
                     or perms.can_maintenance_all or perms.can_maintenance_own)

        row1 = []
        if has_reports:
            row1.append(InlineKeyboardButton("📊 Fleet Reports", callback_data="submenu_reports"))
        if has_tools:
            row1.append(InlineKeyboardButton("🛠 Tools", callback_data="submenu_tools"))
        if row1:
            rows.append(row1)

        row2 = []
        if has_costs:
            row2.append(InlineKeyboardButton("💰 Cost & Maint.", callback_data="submenu_costs"))
        if perms.can_alerts_all or perms.can_alerts_own:
            row2.append(InlineKeyboardButton("🔔 Alerts", callback_data="cmd_alerts"))
        if row2:
            rows.append(row2)

        # Digest — standalone (lightweight)
        if perms.can_digest:
            rows.append([InlineKeyboardButton("📬 Digest", callback_data="cmd_digest")])

        # AI Assistant (visible when API key is configured)
        import ai_client
        if ai_client.is_configured():
            rows.append([InlineKeyboardButton("🤖 AI Assistant", callback_data="cmd_ai")])

        # Driver: show truck shortcut
        if role == Role.DRIVER:
            rows.append([InlineKeyboardButton("🚛 My Truck", callback_data="cmd_mytruck")])

        # Per-company buttons (only when >1 company and role can filter)
        if len(company_codes) > 1 and can_access_company_submenu(role):
            company_row = [
                InlineKeyboardButton(code, callback_data=f"co_{code}")
                for code in company_codes
            ]
            rows.append(company_row)

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
    has_mgmt = (perms.can_manage_account or perms.can_manage_users
                or perms.can_invite or perms.can_manage_companies)
    if has_mgmt:
        rows.append([InlineKeyboardButton("👥 Team & Settings", callback_data="submenu_mgmt")])

    return InlineKeyboardMarkup(rows)


def submenu_reports_kb(role: Role, company_codes: list[str] | None = None) -> InlineKeyboardMarkup:
    """Fleet Reports sub-menu — faults, fuel, health, efficiency, weather, truck."""
    perms = get_permissions(role)
    rows = []

    row1 = []
    if perms.can_faults:
        row1.append(InlineKeyboardButton("🔧 Faults", callback_data="cmd_faults"))
        row1.append(InlineKeyboardButton("🚨 Critical", callback_data="cmd_critical"))
    if row1:
        rows.append(row1)

    row2 = []
    if perms.can_fuel:
        row2.append(InlineKeyboardButton("⛽ Fuel & DEF", callback_data="cmd_fuel"))
    if perms.can_faults:
        row2.append(InlineKeyboardButton("🌡 Weather", callback_data="cmd_weather"))
    if row2:
        rows.append(row2)

    if perms.can_faults:
        rows.append([
            InlineKeyboardButton("🏥 Health", callback_data="cmd_health"),
            InlineKeyboardButton("📊 Efficiency", callback_data="cmd_efficiency"),
        ])

    if perms.can_truck_all:
        rows.append([InlineKeyboardButton("🚛 Search Truck", callback_data="cmd_truck_prompt")])

    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_tools_kb(role: Role) -> InlineKeyboardMarkup:
    """Tools sub-menu — scorecards, map, routes, geofences."""
    perms = get_permissions(role)
    rows = []

    row1 = []
    if perms.can_scorecard_all or perms.can_scorecard_own:
        row1.append(InlineKeyboardButton("🏆 Scorecards", callback_data="cmd_scorecards"))
    if perms.can_location_map or perms.can_location_own:
        row1.append(InlineKeyboardButton("🗺 Live Map", callback_data="cmd_livemap"))
    if row1:
        rows.append(row1)

    row2 = []
    if perms.can_route_all or perms.can_route_own:
        row2.append(InlineKeyboardButton("🛣 Route Replay", callback_data="cmd_route"))
    if perms.can_geofence_all or perms.can_geofence_own:
        row2.append(InlineKeyboardButton("📍 Geofences", callback_data="cmd_geofences"))
    if row2:
        rows.append(row2)

    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_costs_kb(role: Role) -> InlineKeyboardMarkup:
    """Cost & Maintenance sub-menu."""
    perms = get_permissions(role)
    rows = []

    row1 = []
    if perms.can_fuel_cost:
        row1.append(InlineKeyboardButton("💰 Fuel Costs", callback_data="cmd_fuelcost"))
    if perms.can_cost_per_mile:
        row1.append(InlineKeyboardButton("📊 Cost/Mile", callback_data="cmd_costmile"))
    if row1:
        rows.append(row1)

    if perms.can_maintenance_all or perms.can_maintenance_own:
        rows.append([InlineKeyboardButton("🔧 Maintenance", callback_data="cmd_maintenance")])

    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_mgmt_kb(role: Role, has_api: bool = False) -> InlineKeyboardMarkup:
    """Team & Settings sub-menu."""
    perms = get_permissions(role)
    rows = []

    mgmt = []
    if perms.can_manage_account:
        mgmt.append(InlineKeyboardButton("⚙️ Account", callback_data="cmd_account"))
    if perms.can_manage_users:
        mgmt.append(InlineKeyboardButton("👥 Team", callback_data="cmd_users"))
    if mgmt:
        rows.append(mgmt)

    mgmt2 = []
    if perms.can_invite:
        mgmt2.append(InlineKeyboardButton("✉️ Invite", callback_data="cmd_invite_pick"))
    if perms.can_manage_companies and has_api:
        mgmt2.append(InlineKeyboardButton("📡 Add Company", callback_data="cmd_addcompany_prompt"))
    if mgmt2:
        rows.append(mgmt2)

    if has_api and perms.can_manage_account:
        rows.append([InlineKeyboardButton("📡 API Status", callback_data="cmd_api_status")])

    if perms.can_manage_users:
        rows.append([InlineKeyboardButton("💬 Groups", callback_data="cmd_groups")])

    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def faults_menu_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Shown after the faults PDF — drill into Critical or go back."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚨 Critical Only (PDF)", callback_data=f"cmd_critical{suffix}")],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
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
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
    ])


def fuel_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Ask the user to pick PDF or CSV for the Fuel & DEF report."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"fuel_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"fuel_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
    ])


def health_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    """Ask the user to pick PDF or CSV for the Vehicle Health report."""
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"health_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"health_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
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
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
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
    if truck_name and company:
        import ai_client
        if ai_client.is_configured():
            rows.append([
                InlineKeyboardButton("🔧 AI Diagnose", callback_data=f"ai_diag_{company}_{truck_name}"),
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
        [InlineKeyboardButton(
            "� Request Private Access",
            url="https://t.me/Allen_Klein",
        )],
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


# ── New feature keyboards ─────────────────────────────────────────

def scorecard_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"scorecard_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"scorecard_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_tools")],
    ])


def fuelcost_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Log Fill-Up", callback_data="fuelcost_add")],
        [InlineKeyboardButton("📊 Summary", callback_data="fuelcost_summary")],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_costs")],
    ])


def costmile_format_kb(company: str | None = None) -> InlineKeyboardMarkup:
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 PDF Report", callback_data=f"costmile_pdf{suffix}"),
            InlineKeyboardButton("📊 CSV Export", callback_data=f"costmile_csv{suffix}"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_costs")],
    ])


def maintenance_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Task", callback_data="maint_add")],
        [InlineKeyboardButton("📋 View Tasks", callback_data="maint_view")],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_costs")],
    ])


def maintenance_task_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Mark Done", callback_data=f"maint_done_{task_id}"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="maint_view")],
    ])


def digest_menu_kb(current_sub: dict | None = None) -> InlineKeyboardMarkup:
    rows = []
    if current_sub:
        freq = current_sub.get("frequency", "daily")
        hour = current_sub.get("send_hour", 7)
        rows.append([InlineKeyboardButton(
            f"📬 Active: {freq.title()} at {hour:02d}:00 UTC",
            callback_data="noop",
        )])
        rows.append([InlineKeyboardButton("🔕 Unsubscribe", callback_data="digest_unsub")])
    else:
        rows.append([
            InlineKeyboardButton("📅 Daily", callback_data="digest_daily"),
            InlineKeyboardButton("📆 Weekly", callback_data="digest_weekly"),
        ])
    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def livemap_refresh_kb(company: str | None = None) -> InlineKeyboardMarkup:
    suffix = f"_{company}" if company else ""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"cmd_livemap{suffix}")],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_tools")],
    ])


def route_date_kb(vehicle_name: str, company: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Today", callback_data=f"route_go_{company}_{vehicle_name}_0"),
            InlineKeyboardButton("Yesterday", callback_data=f"route_go_{company}_{vehicle_name}_1"),
        ],
        [
            InlineKeyboardButton("2 Days Ago", callback_data=f"route_go_{company}_{vehicle_name}_2"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_tools")],
    ])


def geofence_list_kb(geofences: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for gf in geofences[:15]:
        gid = gf.get("id", "")
        name = gf.get("name", "Unknown")
        rows.append([InlineKeyboardButton(f"📍 {name}", callback_data=f"gf_detail_{gid}")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="submenu_tools")])
    return InlineKeyboardMarkup(rows)


def alert_settings_kb(user) -> InlineKeyboardMarkup:
    """Per-type alert toggle keyboard.

    Shows a checkmark (✅) or cross (❌) for each alert category.
    Tapping a row toggles that category on/off.
    """
    def _icon(on: bool) -> str:
        return "✅" if on else "❌"

    rows = [
        [InlineKeyboardButton(
            f"{_icon(user.alert_faults)} Fault Alerts",
            callback_data="alert_toggle_faults",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_health)} Health Alerts",
            callback_data="alert_toggle_health",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_fuel)} Fuel Alerts",
            callback_data="alert_toggle_fuel",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_geofence)} Geofence Alerts",
            callback_data="alert_toggle_geofence",
        )],
        [InlineKeyboardButton("� Pending Alerts", callback_data="cmd_pending_alerts"),
         InlineKeyboardButton("📜 History", callback_data="cmd_alert_history")],
        [InlineKeyboardButton("�🔕 Disable All Alerts", callback_data="alert_disable_all")],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")],
    ]
    return InlineKeyboardMarkup(rows)
