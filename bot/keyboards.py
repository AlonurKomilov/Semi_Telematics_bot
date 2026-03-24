"""Inline keyboard builders — role-aware."""

from urllib.parse import quote

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, KeyboardButtonRequestChat,
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
)


from bot.i18n import SUPPORTED_LANGUAGES, LANGUAGE_NAMES, LANGUAGE_FLAGS, t
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
                       or perms.can_truck_all or perms.can_truck_own
                       or perms.can_events_all or perms.can_events_own)
        has_tools = (perms.can_scorecard_all or perms.can_scorecard_own
                     or perms.can_location_map or perms.can_location_own
                     or perms.can_route_all or perms.can_route_own
                     or perms.can_geofence_all or perms.can_geofence_own)
        has_costs = (perms.can_fuel_cost or perms.can_cost_per_mile
                     or perms.can_maintenance_all or perms.can_maintenance_own)

        row1 = []
        if has_reports:
            row1.append(InlineKeyboardButton(t("menu.reports"), callback_data="submenu_reports"))
        if has_tools:
            row1.append(InlineKeyboardButton(t("menu.tools"), callback_data="submenu_tools"))
        if row1:
            rows.append(row1)

        row2 = []
        if has_costs:
            row2.append(InlineKeyboardButton(t("menu.costs"), callback_data="submenu_costs"))
        if perms.can_alerts_all or perms.can_alerts_own:
            row2.append(InlineKeyboardButton(t("menu.alerts"), callback_data="cmd_alerts"))
        if row2:
            rows.append(row2)

        # Digest — standalone (lightweight)
        if perms.can_digest:
            rows.append([InlineKeyboardButton(t("menu.digest"), callback_data="cmd_digest")])

        # AI Assistant (visible when API key is configured)
        import ai_client
        if ai_client.is_configured():
            rows.append([InlineKeyboardButton(t("menu.ai_assistant"), callback_data="cmd_ai")])

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
        rows.append([InlineKeyboardButton(t("menu.management"), callback_data="submenu_mgmt")])

    return InlineKeyboardMarkup(rows)


def submenu_reports_kb(role: Role, company_codes: list[str] | None = None) -> InlineKeyboardMarkup:
    """Fleet Reports sub-menu — faults, fuel, health, efficiency, weather, truck."""
    perms = get_permissions(role)
    rows = []

    row1 = []
    if perms.can_faults:
        row1.append(InlineKeyboardButton(t("reports_menu.faults"), callback_data="cmd_faults"))
    if row1:
        rows.append(row1)

    row2 = []
    if perms.can_fuel:
        row2.append(InlineKeyboardButton(t("reports_menu.fuel_def"), callback_data="cmd_fuel"))
    if perms.can_faults:
        row2.append(InlineKeyboardButton("🌡 Weather", callback_data="cmd_weather"))
    if row2:
        rows.append(row2)

    if perms.can_faults:
        rows.append([
            InlineKeyboardButton(t("reports_menu.vehicle_health"), callback_data="cmd_health"),
            InlineKeyboardButton(t("reports_menu.efficiency"), callback_data="cmd_efficiency"),
        ])

    if perms.can_events_all or perms.can_events_own:
        rows.append([InlineKeyboardButton(t("tools_menu.events"), callback_data="cmd_events")])

    if perms.can_truck_all:
        rows.append([InlineKeyboardButton(t("reports_menu.search_truck"), callback_data="cmd_truck_prompt")])

    if perms.can_digest:
        rows.append([InlineKeyboardButton(t("menu.digest"), callback_data="cmd_auto_reports")])

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_tools_kb(role: Role) -> InlineKeyboardMarkup:
    """Tools sub-menu — scorecards, map, routes, geofences."""
    perms = get_permissions(role)
    rows = []

    row1 = []
    if perms.can_scorecard_all or perms.can_scorecard_own:
        row1.append(InlineKeyboardButton(t("reports_menu.scorecards"), callback_data="cmd_scorecards"))
    if perms.can_location_map or perms.can_location_own:
        row1.append(InlineKeyboardButton(t("reports_menu.live_map"), callback_data="cmd_livemap"))
    if row1:
        rows.append(row1)

    row2 = []
    if perms.can_route_all or perms.can_route_own:
        row2.append(InlineKeyboardButton("🛣 Route Replay", callback_data="cmd_route"))
    if perms.can_geofence_all or perms.can_geofence_own:
        row2.append(InlineKeyboardButton("📍 Geofences", callback_data="cmd_geofences"))
    if row2:
        rows.append(row2)

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_costs_kb(role: Role) -> InlineKeyboardMarkup:
    """Cost & Maintenance sub-menu."""
    perms = get_permissions(role)
    rows = []

    row1 = []
    if perms.can_fuel_cost:
        row1.append(InlineKeyboardButton(t("costs_menu.fuel_costs"), callback_data="cmd_fuelcost"))
    if perms.can_cost_per_mile:
        row1.append(InlineKeyboardButton("📊 Cost/Mile", callback_data="cmd_costmile"))
    if row1:
        rows.append(row1)

    if perms.can_maintenance_all or perms.can_maintenance_own:
        rows.append([InlineKeyboardButton(t("costs_menu.maintenance"), callback_data="cmd_maintenance")])

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_mgmt_kb(role: Role, has_api: bool = False) -> InlineKeyboardMarkup:
    """Team & Settings sub-menu — top-level navigation."""
    perms = get_permissions(role)
    rows = []

    # ── Main sections ───────────────────────────────────
    top_row = []
    if perms.can_manage_account:
        top_row.append(InlineKeyboardButton(t("mgmt_menu.account_info"), callback_data="cmd_account"))
    if perms.can_manage_users:
        top_row.append(InlineKeyboardButton(t("mgmt_menu.team"), callback_data="cmd_users"))
    if top_row:
        rows.append(top_row)

    # ── Activity ────────────────────────────────────────────
    if perms.can_manage_users:
        rows.append([InlineKeyboardButton("📜 Audit Log", callback_data="cmd_audit")])

    # ── Personal ────────────────────────────────────────────
    rows.append([InlineKeyboardButton(t("menu.settings"), callback_data="cmd_settings")])
    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
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
        [InlineKeyboardButton(t("menu.back_main"), callback_data="cmd_menu")],
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
            "🔒 Request Access",
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


def onboarding_kb() -> InlineKeyboardMarkup:
    """Quick-start onboarding keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Integration Guide", callback_data="cmd_integrate_guide")],
        [InlineKeyboardButton("🕐 Set Timezone", callback_data="settings_tz")],
        [InlineKeyboardButton("🌙 Set Quiet Hours", callback_data="settings_quiet_set")],
        [InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")],
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
    rows.append([InlineKeyboardButton("◀️ Back to Team", callback_data="cmd_users")])
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


def events_format_kb() -> InlineKeyboardMarkup:
    """Period + format picker for Events dashboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("7 Days", callback_data="events_text_7"),
            InlineKeyboardButton("14 Days", callback_data="events_text_14"),
            InlineKeyboardButton("30 Days", callback_data="events_text_30"),
        ],
        [
            InlineKeyboardButton("📊 CSV 7d", callback_data="events_csv_7"),
            InlineKeyboardButton("📊 CSV 14d", callback_data="events_csv_14"),
            InlineKeyboardButton("📊 CSV 30d", callback_data="events_csv_30"),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
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


def auto_reports_menu_kb(current_sub: dict | None = None) -> InlineKeyboardMarkup:
    """Auto Reports subscription menu."""
    rows = []
    if current_sub:
        rtype = current_sub.get("report_type", "faults")
        freq = current_sub.get("frequency", "daily")
        hour = current_sub.get("send_hour", 7)
        tz = current_sub.get("timezone", "UTC")
        tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
        type_labels = {
            "faults": "🔧 Faults",
            "fuel": "⛽ Fuel & DEF",
            "health": "🏥 Vehicle Health",
            "efficiency": "📊 Efficiency",
        }
        label = type_labels.get(rtype, rtype.title())
        rows.append([InlineKeyboardButton(
            f"📋 Active: {label} · {freq.title()} at {hour:02d}:00 {tz_short}",
            callback_data="noop",
        )])
        rows.append([InlineKeyboardButton("🔕 Unsubscribe", callback_data="ar_unsub")])
    else:
        rows.append([
            InlineKeyboardButton("📅 Daily", callback_data="ar_freq_daily"),
            InlineKeyboardButton("📆 Weekly", callback_data="ar_freq_weekly"),
        ])
        rows.append([
            InlineKeyboardButton("📅 Monthly", callback_data="ar_freq_monthly"),
        ])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")])
    return InlineKeyboardMarkup(rows)


def auto_reports_type_kb() -> InlineKeyboardMarkup:
    """Report type picker for Auto Reports."""
    rows = [
        [
            InlineKeyboardButton("🔧 Faults", callback_data="ar_type_faults"),
            InlineKeyboardButton("⛽ Fuel & DEF", callback_data="ar_type_fuel"),
        ],
        [
            InlineKeyboardButton("🏥 Health", callback_data="ar_type_health"),
            InlineKeyboardButton("📊 Efficiency", callback_data="ar_type_efficiency"),
        ],
        [InlineKeyboardButton("◀️ Cancel", callback_data="cmd_auto_reports")],
    ]
    return InlineKeyboardMarkup(rows)


def auto_reports_hour_kb() -> InlineKeyboardMarkup:
    """Hour picker for Auto Reports delivery time."""
    rows = [
        [
            InlineKeyboardButton("6 AM", callback_data="ar_hour_6"),
            InlineKeyboardButton("7 AM", callback_data="ar_hour_7"),
            InlineKeyboardButton("8 AM", callback_data="ar_hour_8"),
        ],
        [
            InlineKeyboardButton("9 AM", callback_data="ar_hour_9"),
            InlineKeyboardButton("12 PM", callback_data="ar_hour_12"),
            InlineKeyboardButton("6 PM", callback_data="ar_hour_18"),
        ],
        [InlineKeyboardButton("◀️ Cancel", callback_data="cmd_auto_reports")],
    ]
    return InlineKeyboardMarkup(rows)


def auto_reports_tz_kb() -> InlineKeyboardMarkup:
    """Timezone picker for Auto Reports delivery."""
    timezones = [
        ("Eastern", "America/New_York"),
        ("Central", "America/Chicago"),
        ("Mountain", "America/Denver"),
        ("Pacific", "America/Los_Angeles"),
        ("UTC", "UTC"),
    ]
    rows = [
        [InlineKeyboardButton(f"🕐 {label}", callback_data=f"ar_tz_{tz}")]
        for label, tz in timezones
    ]
    rows.append([InlineKeyboardButton("◀️ Cancel", callback_data="cmd_auto_reports")])
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
            f"{_icon(user.alert_faults)} {t('alert_settings.faults')}",
            callback_data="alert_toggle_faults",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_health)} {t('alert_settings.health')}",
            callback_data="alert_toggle_health",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_fuel)} {t('alert_settings.fuel')}",
            callback_data="alert_toggle_fuel",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_geofence)} {t('alert_settings.geofence')}",
            callback_data="alert_toggle_geofence",
        )],
        [InlineKeyboardButton(
            f"{_icon(user.alert_events)} {t('alert_settings.events')}",
            callback_data="alert_toggle_events",
        )],
        [InlineKeyboardButton(t("alert_settings.pending"), callback_data="cmd_pending_alerts"),
         InlineKeyboardButton(t("alert_settings.history"), callback_data="cmd_alert_history")],
        [InlineKeyboardButton(t("alert_settings.disable_all"), callback_data="alert_disable_all")],
        [InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def events_format_kb() -> InlineKeyboardMarkup:
    """Events period/format picker keyboard."""
    rows = [
        [InlineKeyboardButton("📊 7 Days", callback_data="events_text_7"),
         InlineKeyboardButton("📊 14 Days", callback_data="events_text_14"),
         InlineKeyboardButton("📊 30 Days", callback_data="events_text_30")],
        [InlineKeyboardButton("📄 CSV 7d", callback_data="events_csv_7"),
         InlineKeyboardButton("📄 CSV 14d", callback_data="events_csv_14"),
         InlineKeyboardButton("📄 CSV 30d", callback_data="events_csv_30")],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_reports")],
    ]
    return InlineKeyboardMarkup(rows)


def user_settings_kb(user) -> InlineKeyboardMarkup:
    """User settings menu with quiet hours, timezone, and language."""
    quiet = ""
    if getattr(user, "quiet_start", None) is not None and getattr(user, "quiet_end", None) is not None:
        quiet = f" ({user.quiet_start:02d}:00–{user.quiet_end:02d}:00)"
    tz = getattr(user, "timezone", None) or "Not set"
    tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
    lang_code = getattr(user, "language", "en") or "en"
    lang_flag = LANGUAGE_FLAGS.get(lang_code, "🌐")
    lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
    rows = [
        [InlineKeyboardButton(f"🌙 {t('user_settings.notifications')}{quiet}", callback_data="settings_quiet")],
        [InlineKeyboardButton(f"🕐 {t('user_settings.timezone')}: {tz_short}", callback_data="settings_tz")],
        [InlineKeyboardButton(f"{lang_flag} {t('user_settings.language')}: {lang_name}", callback_data="settings_lang")],
        [InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")],
    ]
    return InlineKeyboardMarkup(rows)


def language_kb(current: str = "en", region: str | None = None) -> InlineKeyboardMarkup:
    """Language picker keyboard with sub-menus for layout compactness."""
    rows = []
    
    if region == "africa":
        langs = ["so", "am"]
        for code in langs:
            flag = LANGUAGE_FLAGS[code]
            name = LANGUAGE_NAMES[code]
            check = " ✓" if code == current else ""
            rows.append([InlineKeyboardButton(f"{flag} {name}{check}", callback_data=f"set_lang_{code}")])
        rows.append([InlineKeyboardButton(t("common.back"), callback_data="settings_lang")])
    elif region == "eurasia":
        langs = ["ru", "uk", "uz", "pa"]
        for code in langs:
            flag = LANGUAGE_FLAGS[code]
            name = LANGUAGE_NAMES[code]
            check = " ✓" if code == current else ""
            rows.append([InlineKeyboardButton(f"{flag} {name}{check}", callback_data=f"set_lang_{code}")])
        rows.append([InlineKeyboardButton(t("common.back"), callback_data="settings_lang")])
    else:
        # Top level
        langs = ["en", "es", "fr"]
        for code in langs:
            flag = LANGUAGE_FLAGS[code]
            name = LANGUAGE_NAMES[code]
            check = " ✓" if code == current else ""
            rows.append([InlineKeyboardButton(f"{flag} {name}{check}", callback_data=f"set_lang_{code}")])
        
        # Sub-menus
        rows.append([InlineKeyboardButton("🌍 East African...", callback_data="lang_region_africa")])
        rows.append([InlineKeyboardButton("🌏 Eastern Europe & Asia...", callback_data="lang_region_eurasia")])
        rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_settings")])

    return InlineKeyboardMarkup(rows)


def quiet_hours_kb(user) -> InlineKeyboardMarkup:
    """DND quiet hours settings."""
    active = getattr(user, "quiet_start", None) is not None
    rows = []
    if active:
        rows.append([InlineKeyboardButton(
            f"🌙 Active: {user.quiet_start:02d}:00–{user.quiet_end:02d}:00",
            callback_data="settings_quiet_set",
        )])
        rows.append([InlineKeyboardButton("❌ Disable Quiet Hours", callback_data="settings_quiet_off")])
    else:
        rows.append([InlineKeyboardButton("🌙 Enable Quiet Hours", callback_data="settings_quiet_set")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_settings")])
    return InlineKeyboardMarkup(rows)


def quiet_hours_picker_kb() -> InlineKeyboardMarkup:
    """Time preset picker for quiet hours."""
    rows = [
        [InlineKeyboardButton("🌙 10 PM – 6 AM", callback_data="quiet_set_22_6")],
        [InlineKeyboardButton("🌙 9 PM – 6 AM", callback_data="quiet_set_21_6")],
        [InlineKeyboardButton("🌙 11 PM – 7 AM", callback_data="quiet_set_23_7")],
        [InlineKeyboardButton("🌙 8 PM – 5 AM", callback_data="quiet_set_20_5")],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_settings")],
    ]
    return InlineKeyboardMarkup(rows)


def settings_tz_kb() -> InlineKeyboardMarkup:
    """Timezone selection keyboard."""
    timezones = [
        ("Eastern", "America/New_York"),
        ("Central", "America/Chicago"),
        ("Mountain", "America/Denver"),
        ("Pacific", "America/Los_Angeles"),
        ("Alaska", "America/Anchorage"),
        ("Hawaii", "Pacific/Honolulu"),
        ("UTC", "UTC"),
    ]
    rows = [
        [InlineKeyboardButton(f"🕐 {label}", callback_data=f"set_tz_{tz}")]
        for label, tz in timezones
    ]
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_settings")])
    return InlineKeyboardMarkup(rows)
