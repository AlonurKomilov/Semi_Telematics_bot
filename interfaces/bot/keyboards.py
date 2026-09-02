"""Inline keyboard builders — role-aware."""

from urllib.parse import quote

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, KeyboardButtonRequestChat,
    ReplyKeyboardMarkup, WebAppInfo,
)


from capabilities.localization.i18n import LANGUAGE_NAMES, LANGUAGE_FLAGS, t
from adapters.storage import Role
from capabilities.permissions.roles import get_permissions, can_access_company_submenu
from infra.context import get_company_display


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
        # Predicates match exactly what each submenu now renders
        # post-dashboard-migration — hiding the parent button when
        # the submenu would otherwise be empty for that role.
        #
        # Tools and Costs submenus were collapsed: each only had one
        # surviving button (Parking and Fuel Costs respectively)
        # after the dashboard migration, so we promote them to
        # top-level instead of forcing the user through an empty
        # wrapper.  The submenu_* builders below still exist as
        # fallbacks for cached buttons.
        has_reports = (perms.can_vehicle_all
                       or perms.can_view_events)
        has_parking = perms.can_view_parking
        has_fuel_cost = perms.can_fuel_cost

        row1 = []
        if has_reports:
            row1.append(InlineKeyboardButton(t("menu.reports"), callback_data="submenu_reports"))
        if has_parking:
            row1.append(InlineKeyboardButton("🅿️ Parking", callback_data="cmd_parking_events"))
        if row1:
            rows.append(row1)

        row2 = []
        if has_fuel_cost:
            row2.append(InlineKeyboardButton(t("costs_menu.fuel_costs"), callback_data="cmd_fuelcost"))
        if perms.can_view_vehicles:
            row2.append(InlineKeyboardButton(t("menu.alerts"), callback_data="cmd_alerts"))
        if row2:
            rows.append(row2)

        # AI Assistant (visible when API key is configured)
        import capabilities.ai as ai
        if ai.is_configured():
            rows.append([InlineKeyboardButton(t("menu.ai_assistant"), callback_data="cmd_ai")])

        # Live Map Mini App (visible when WEBAPP_URL is configured)
        from interfaces.bot.config import WEBAPP_URL
        if WEBAPP_URL and (perms.can_view_location):
            rows.append([InlineKeyboardButton(
                "🗺 Live Map",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}#map"),
            )])

        # Driver: show truck shortcut
        if role == Role.DRIVER:
            rows.append([InlineKeyboardButton("🚛 My Vehicle", callback_data="cmd_myvehicle")])

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

    # ── Management & Settings (bottom row) ─────────────────────
    has_mgmt = (perms.can_manage_account or perms.can_manage_users
                or perms.can_invite or perms.can_manage_companies)
    bottom = []
    if has_mgmt:
        bottom.append(InlineKeyboardButton(t("menu.management"), callback_data="submenu_mgmt"))
    bottom.append(InlineKeyboardButton(t("menu.settings"), callback_data="cmd_settings"))
    rows.append(bottom)

    return InlineKeyboardMarkup(rows)


def submenu_reports_kb(role: Role, company_codes: list[str] | None = None) -> InlineKeyboardMarkup:  # noqa: ARG001
    """Reports sub-menu — quick bot lookups only.

    Fleet-wide report buttons (Faults / Fuel / Health / Efficiency /
    Weather / Cameras report / Scheduled digest) were retired —
    those flows live on the dashboard now.  Typing the matching
    slash command still works and redirects.  What's kept here is
    the moment-shaped stuff: a single-event browse and a single-
    truck lookup.
    """
    perms = get_permissions(role)
    rows = []

    if perms.can_view_events:
        rows.append([InlineKeyboardButton(t("tools_menu.events"), callback_data="cmd_events")])

    if perms.can_vehicle_all:
        rows.append([InlineKeyboardButton(t("reports_menu.search_truck"), callback_data="cmd_vehicle_prompt")])

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_tools_kb(role: Role) -> InlineKeyboardMarkup:
    """Tools sub-menu — only the bot-shaped action lookups.

    Scorecards / Live Map / Routes / Geofences / Maintenance / the
    paginated Cameras picker all moved to the dashboard.  What
    stays here is the per-driver / per-event view that the bot is
    actually useful for.
    """
    perms = get_permissions(role)
    rows = []

    # Parking — driver-scoped event view (Parking's own permission)
    if perms.can_view_parking:
        rows.append([InlineKeyboardButton("🅿️ Parking", callback_data="cmd_parking_events")])

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_costs_kb(role: Role) -> InlineKeyboardMarkup:
    """Costs sub-menu — driver fill-up logging only.

    Cost-per-mile reports moved to the dashboard.  ``/fuelcost`` is
    kept because the *Add fill-up* step is a driver-at-the-pump
    moment (pump receipt in hand, quick entry, done).
    """
    perms = get_permissions(role)
    rows = []

    if perms.can_fuel_cost:
        rows.append([InlineKeyboardButton(t("costs_menu.fuel_costs"), callback_data="cmd_fuelcost")])

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def submenu_mgmt_kb(role: Role, has_api: bool = False) -> InlineKeyboardMarkup:
    """Team & Management sub-menu — account, invite, audit."""
    perms = get_permissions(role)
    rows = []

    # ── Main sections ───────────────────────────────────
    top_row = []
    if perms.can_manage_account:
        top_row.append(InlineKeyboardButton(t("mgmt_menu.account_info"), callback_data="cmd_account"))
    # Team / Users grid removed — full role / vehicle assignment + bulk
    # actions live on the dashboard.  Invite stays bot-side because
    # the Telegram-channel deep-link only makes sense from the bot —
    # operators wanting URL/Email channels use the dashboard.
    if perms.can_invite:
        top_row.append(InlineKeyboardButton("✉️ Invite", callback_data="cmd_invite"))
    if top_row:
        rows.append(top_row)

    # ── Activity ────────────────────────────────────────────
    if perms.can_manage_users:
        rows.append([InlineKeyboardButton("📜 Audit Log", callback_data="cmd_audit")])

    # Working Hours config moved to the dashboard.  Typing
    # /work_hours still works and replies with a dashboard deep-link.

    rows.append([InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


# faults_menu_kb was the post-PDF action keyboard for cmd_faults
# (Critical drill-down + Back).  Retired with the rest of the
# fleet PDF reports; cmd_faults now redirects to the dashboard.


def co_menu_kb(company: str) -> InlineKeyboardMarkup:  # noqa: ARG001
    """Per-company sub-menu — only the back button after cleanup.

    All five per-company report shortcuts (Faults, Fuel, Efficiency,
    Vehicle Health, Weather) led to commands that now redirect to
    the dashboard.  Keeping the buttons here only meant 2 taps
    (company picker → per-company button) to reach the same
    redirect message — worse UX than a single tap from the main
    menu.  The submenu still exists so the ``co_<CODE>`` callback
    has a landing card; the dashboard handles per-company filters
    natively via its company picker.
    """
    return InlineKeyboardMarkup([
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


def vehicle_kb(
    vehicle_name: str | None = None,
    company: str | None = None,
    show_faults: bool = True,
    ack_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Buttons shown below a single-truck detail.

    *show_faults* — when False (Dispatcher / Driver) the PDF report
    button is hidden so role restrictions are enforced visually as
    well as logically.
    *ack_id* — when set, shows a Back to Alert button instead of Main Menu.
    """
    rows = []
    if show_faults and vehicle_name and company:
        rows.append([
            InlineKeyboardButton("📄 PDF Report", callback_data=f"vehiclefaults_{company}_{vehicle_name}"),
        ])
    if vehicle_name and company:
        import capabilities.ai as ai
        if ai.is_configured():
            rows.append([
                InlineKeyboardButton("🔧 AI Diagnose", callback_data=f"ai_diag_{company}_{vehicle_name}"),
            ])
        # Per-truck Camera Check button was removed when the whole
        # camera surface moved to the dashboard.  Operators run
        # camera checks on dash.4truck.us/cameras now.  Camera
        # *alerts* still ping via the scheduled job.
    if ack_id is not None:
        rows.append([InlineKeyboardButton("↩️ Back to Alert", callback_data=f"back_alert_{ack_id}")])
    else:
        rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def vehicle_picker_kb(matches: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for v in matches:
        co = v.get("_org", "?")
        name = v["name"]
        label = f"#{name} — {get_company_display().get(co, co)} ({co})"
        rows.append([InlineKeyboardButton(label, callback_data=f"covehicle_{co}_{name}")])
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
            InlineKeyboardButton("🤖 AI Stats", callback_data="sys_ai_stats"),
            InlineKeyboardButton("🖥 Server", callback_data="sys_server"),
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


# The entire camera surface (cam_company_picker_kb,
# cam_vehicle_list_kb, the per-truck Camera Check button on vehicle
# detail, cmd_cam_tool / cmd_cam / cmd_camera_check / cmd_camera_history)
# is retired from the bot.  Camera *alerts* still fire through the
# scheduled job; the interactive surface lives at
# dash.4truck.us/cameras.


def vehicle_company_picker_kb(company_codes: list[str]) -> InlineKeyboardMarkup:
    """Let user pick a company before browsing trucks, or show all."""
    rows = []
    if len(company_codes) > 1:
        rows.append([InlineKeyboardButton(
            "🚛 All Companies", callback_data="vehicles_browse_ALL",
        )])
    for code in company_codes:
        display = get_company_display().get(code, code)
        rows.append([InlineKeyboardButton(
            f"🚛 {display} ({code})", callback_data=f"vehicles_browse_{code}",
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
        rows.append([InlineKeyboardButton(label, callback_data=f"covehicle_{co}_{name}")])

    # Pagination
    nav = []
    prefix = f"vehicles_page_{company_filter or 'ALL'}"
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"{prefix}_{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"{prefix}_{page + 1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton("◀️ Main Menu", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


# ── New feature keyboards ─────────────────────────────────────────

# scorecard_format_kb / costmile_format_kb were the "PDF or CSV?"
# pickers shown after the bot-side commands.  Both report flows
# moved to the dashboard along with the file generators.


def fuelcost_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Log Fill-Up", callback_data="fuelcost_add")],
        [InlineKeyboardButton("📊 Summary", callback_data="fuelcost_summary")],
        [InlineKeyboardButton("◀️ Back", callback_data="submenu_costs")],
    ])


# Maintenance CRUD keyboards (maintenance_menu_kb, maint_company_picker_kb,
# maint_vehicle_list_kb, maint_type_kb, maint_due_kb, maint_miles_kb,
# maint_hours_kb, maint_desc_kb, maint_priority_kb, maint_recur_kb,
# maint_task_detail_kb, maint_edit_kb, maint_delete_confirm_kb,
# maint_task_list_kb, maintenance_task_kb) were removed when the bot
# CRUD surface moved to the dashboard.  The inline "✓ Mark Done"
# button on scheduler-posted alerts is built directly in
# interfaces/bot/maintenance.py via _done_kb().


def scheduled_reports_menu_kb(subs: list[dict] | dict | None = None) -> InlineKeyboardMarkup:
    """Scheduled Reports main menu — list view with multi-schedule.

    Accepts either a list of schedule dicts (multi-schedule model,
    2026-06+) OR a single dict / None for backward compat with callers
    that haven't migrated yet.  Normalises to a list internally.

    When the user has zero schedules: shows the frequency picker
    inline (Daily/Weekly/Monthly) so the first-time experience is one
    tap from the main menu.

    When the user has at least one schedule: shows a "🔕 Stop {label}"
    button per row + an "+ Add new schedule" entry that re-opens the
    frequency picker.  Each per-row stop uses callback prefix
    ``ar_stop_<report_type>`` (handled by ``_ar_stop_per_type`` in
    callbacks/__init__.py); the legacy exact callback ``ar_unsub``
    still works (it stops every schedule the user has — kept for the
    in-flight buttons baked into older messages).
    """
    rows = []
    # Normalise to a list so we can iterate uniformly.
    if subs is None:
        subs_list: list[dict] = []
    elif isinstance(subs, dict):
        subs_list = [subs]
    else:
        subs_list = list(subs)

    if subs_list:
        # Derived from the canonical report registry — guarantees this
        # menu never drifts from the dashboard tabs or the bot scheduler.
        from capabilities.reporting.registry import REPORTS as _R
        type_labels = {r.key: r.label_with_emoji for r in _R}
        # One row per schedule with a per-row Stop button.  The
        # detail line (frequency / hour / tz) lives in the message
        # text above the keyboard so the buttons stay short.
        for sub in subs_list:
            rtype = sub.get("report_type", "faults")
            label = type_labels.get(rtype, rtype.title())
            rows.append([InlineKeyboardButton(
                f"🔕 Stop {label}",
                callback_data=f"ar_stop_{rtype}",
            )])
        rows.append([InlineKeyboardButton(
            "➕ Add new schedule",
            callback_data="ar_add",
        )])
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


def scheduled_reports_type_kb() -> InlineKeyboardMarkup:
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
        [InlineKeyboardButton("📷 Camera Check", callback_data="ar_type_camera")],
        [InlineKeyboardButton("◀️ Cancel", callback_data="cmd_auto_reports")],
    ]
    return InlineKeyboardMarkup(rows)


def scheduled_reports_hour_kb() -> InlineKeyboardMarkup:
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


def scheduled_reports_tz_kb() -> InlineKeyboardMarkup:
    """Timezone picker for Auto Reports delivery.

    Kept aligned with the dashboard's 4-zone whitelist
    (``capabilities.localization.tz.IANA_OPTIONS``) so users can't
    pick a value the backend would reject.  US-only product → no
    UTC / Alaska / Hawaii.
    """
    timezones = [
        ("Pacific",  "America/Los_Angeles"),
        ("Mountain", "America/Denver"),
        ("Central",  "America/Chicago"),
        ("Eastern",  "America/New_York"),
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


def alert_settings_kb(user, relevant: set | None = None) -> InlineKeyboardMarkup:
    """Per-type alert toggle keyboard, role-filtered.

    Shows a checkmark (✅) or cross (❌) for each alert category.
    Tapping a row toggles that category on/off.

    The visible toggle list is narrowed by
    ``capabilities/alerting/relevance.alert_types_for_role`` — a
    Safety user doesn't see a Fuel toggle, a Dispatcher doesn't see
    Health, etc.  The subscriber-fetch path
    (``adapters/storage/users.get_typed_alert_subscribers``) applies
    the same filter so a stale "on" toggle for an irrelevant type
    can't leak alerts the user shouldn't get.

    The "🟢 Resolved notifications" toggle is always shown (every role
    can opt in to receive resolved-alert confirmation DMs).  Defaults
    to OFF.
    """
    from capabilities.alerting.relevance import alert_types_for_role

    # ``relevant`` is the caller's EFFECTIVE per-account type list
    # (§9d) — the async handler resolves it; the static role list is
    # only the fallback for legacy call sites.
    def _icon(on: bool) -> str:
        return "✅" if on else "❌"

    # Display-friendly label + the user-prefs column each alert type
    # maps to.  Order matches ALERT_TYPE_REQUIRED_PERM for consistency
    # across the bot and dashboard.
    _ROWS: list[tuple[str, str, str, str]] = [
        # (alert_type, user-attr, callback, label)
        ("faults",   "alert_faults",   "alert_toggle_faults",   t("alert_settings.faults")),
        ("health",   "alert_health",   "alert_toggle_health",   t("alert_settings.health")),
        ("fuel",     "alert_fuel",     "alert_toggle_fuel",     t("alert_settings.fuel")),
        ("geofence", "alert_geofence", "alert_toggle_geofence", t("alert_settings.geofence")),
        ("events",   "alert_events",   "alert_toggle_events",   t("alert_settings.events")),
        ("camera",   "alert_camera",   "alert_toggle_camera",   "📷 Camera"),
        ("parking",  "alert_parking",  "alert_toggle_parking",  t("alert_settings.parking")),
    ]

    if relevant is None:
        relevant = set(alert_types_for_role(user.role))
    rows: list[list[InlineKeyboardButton]] = []
    for alert_type, attr, callback, label in _ROWS:
        if alert_type not in relevant:
            continue
        rows.append([InlineKeyboardButton(
            f"{_icon(getattr(user, attr, True))} {label}",
            callback_data=callback,
        )])

    # Resolved-alert notification toggle (per-user DM opt-in).
    # Always shown regardless of role — every role can choose whether
    # they want the 🟢 RESOLVED follow-up in their personal chat.
    rows.append([InlineKeyboardButton(
        f"{_icon(getattr(user, 'alert_resolve_receipts', False))} "
        "🟢 Resolved notifications",
        callback_data="alert_toggle_resolve_receipts",
    )])

    rows.extend([
        [InlineKeyboardButton(t("alert_settings.pending"), callback_data="cmd_pending_alerts"),
         InlineKeyboardButton(t("alert_settings.history"), callback_data="cmd_alert_history")],
        [InlineKeyboardButton(t("alert_settings.disable_all"), callback_data="alert_disable_all")],
        [InlineKeyboardButton(t("menu.back"), callback_data="cmd_menu")],
    ])
    return InlineKeyboardMarkup(rows)


def parking_events_kb(
    events: list[dict], show_all: bool = False,
) -> InlineKeyboardMarkup:
    """Keyboard listing active unsafe parking events.

    show_all=False (default): attention-only view (unsafe + unknown).
    show_all=True: all stopped vehicles including safe/geofenced.
    """
    rows = []
    for ev in events[:10]:
        vname = ev.get("vehicle_name", "?")
        dur = ev.get("duration_hours", 0)
        loc_class = ev.get("location_class", "unknown")
        icon = {"unsafe": "🔴", "unknown": "🟡", "safe": "🟢", "geofence": "🟢"}.get(loc_class, "🟡")
        if dur >= 24:
            dur_str = f"{dur / 24:.1f}d"
        elif dur < 1:
            dur_str = f"{int(dur * 60)}m"
        else:
            dur_str = f"{dur:.1f}h"
        rows.append([InlineKeyboardButton(
            f"{icon} #{vname} — {dur_str}",
            callback_data=f"parking_detail_{ev.get('id', 0)}",
        )])
    if not events:
        rows.append([InlineKeyboardButton("✅ No vehicles need attention", callback_data="noop")])
    # N2 — Tab toggle
    if show_all:
        rows.append([InlineKeyboardButton("🔍 Attention Only", callback_data="cmd_parking_events")])
    else:
        rows.append([InlineKeyboardButton("📋 All Stopped", callback_data="cmd_parking_all")])
    rows.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="cmd_parking_events"),
        InlineKeyboardButton("📅 History", callback_data="cmd_parking_history"),
    ])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_menu")])
    return InlineKeyboardMarkup(rows)


def parking_history_kb(days: int = 7) -> InlineKeyboardMarkup:
    """Keyboard for the parking history tab with period selector."""
    rows = [
        [
            InlineKeyboardButton(
                f"{'✓ ' if days == 7 else ''}7 Days",
                callback_data="cmd_parking_history_7",
            ),
            InlineKeyboardButton(
                f"{'✓ ' if days == 30 else ''}30 Days",
                callback_data="cmd_parking_history_30",
            ),
        ],
        [InlineKeyboardButton("◀️ Back", callback_data="cmd_parking_events")],
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
    """User settings menu with working hours, timezone, and language."""
    work = ""
    if getattr(user, "quiet_start", None) is not None and getattr(user, "quiet_end", None) is not None:
        work = f" ({user.quiet_start:02d}:00–{user.quiet_end:02d}:00)"
    tz = getattr(user, "timezone", None) or "Not set"
    tz_short = tz.split("/")[-1].replace("_", " ") if "/" in tz else tz
    lang_code = getattr(user, "language", "en") or "en"
    lang_flag = LANGUAGE_FLAGS.get(lang_code, "🌐")
    lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
    # Personal DND label changed from "Working Hours" to "Quiet Hours"
    # to avoid collision with the admin "Working Hours" menu (team shift
    # templates).  Personal = DND silencer for one user; admin's
    # Working Hours = team schedule that drives the default DND when
    # the user has no personal override.
    rows = [
        [InlineKeyboardButton(f"🌙 {t('user_settings.quiet_hours', default='Quiet Hours')}{work}", callback_data="settings_quiet")],
        [InlineKeyboardButton(f"🌐 {t('user_settings.timezone')}: {tz_short}", callback_data="settings_tz")],
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
    """Quiet-hours / DND settings (employee view).

    Two states the user can be in:
      • ``Personal override`` — they set their own ``quiet_start/end``
        and those win over the team Working Hours.  Show the active
        window + a "clear" button.
      • ``Auto from Working Hours`` — neither column set; DND derives
        from the account's Working Hours for this user's role.  Show
        a "set personal override" button.
    """
    active = getattr(user, "quiet_start", None) is not None and getattr(user, "quiet_end", None) is not None
    rows = []
    if active:
        rows.append([InlineKeyboardButton(
            f"🌙 Personal override: {user.quiet_start:02d}:00–{user.quiet_end:02d}:00",
            callback_data="settings_quiet_set",
        )])
        rows.append([InlineKeyboardButton(
            "♻️ Clear override (use team Working Hours)",
            callback_data="settings_quiet_off",
        )])
    else:
        rows.append([InlineKeyboardButton(
            "⏰ Using team Working Hours",
            callback_data="settings_quiet_set",
        )])
        rows.append([InlineKeyboardButton(
            "🌙 Set personal override",
            callback_data="settings_quiet_set",
        )])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_settings")])
    return InlineKeyboardMarkup(rows)


def quiet_hours_picker_kb(schedules: list[dict] | None = None) -> InlineKeyboardMarkup:
    """Working hours preset picker.

    If account-level schedules exist, show those.
    Otherwise fall back to hardcoded defaults.
    """
    rows = []
    if schedules:
        for s in schedules:
            start, end = s["start_hour"], s["end_hour"]
            label = s.get("label") or f"{start:02d}:00–{end:02d}:00"
            rows.append([InlineKeyboardButton(
                f"🕐 {label} ({start}:00–{end}:00)",
                callback_data=f"quiet_set_{start}_{end}",
            )])
    else:
        rows = [
            [InlineKeyboardButton("🕐 6 AM – 10 PM", callback_data="quiet_set_6_22")],
            [InlineKeyboardButton("🕐 6 AM – 9 PM", callback_data="quiet_set_6_21")],
            [InlineKeyboardButton("🕐 7 AM – 11 PM", callback_data="quiet_set_7_23")],
            [InlineKeyboardButton("🕐 5 AM – 8 PM", callback_data="quiet_set_5_20")],
        ]
    rows.append([InlineKeyboardButton("🕐 24/7 (Always On)", callback_data="settings_quiet_off")])
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="settings_quiet")])
    return InlineKeyboardMarkup(rows)


# work_hours_kb / work_hour_detail_kb / work_hour_picker_kb /
# work_hour_role_picker_kb were the schedule-editor keyboards driven
# by cmd_whours_* — all retired with the /work_hours dashboard
# redirect.  The i18n keys they referenced (work_hours.add /
# edit_label / edit_hours / delete / max_reached) are dropped in
# the same pass.


def settings_tz_kb() -> InlineKeyboardMarkup:
    """Timezone selection keyboard.

    Matches the dashboard's 4-zone whitelist
    (``capabilities.localization.tz.IANA_OPTIONS``).
    """
    timezones = [
        ("Pacific",  "America/Los_Angeles"),
        ("Mountain", "America/Denver"),
        ("Central",  "America/Chicago"),
        ("Eastern",  "America/New_York"),
    ]
    rows = [
        [InlineKeyboardButton(f"🕐 {label}", callback_data=f"set_tz_{tz}")]
        for label, tz in timezones
    ]
    rows.append([InlineKeyboardButton("◀️ Back", callback_data="cmd_settings")])
    return InlineKeyboardMarkup(rows)
