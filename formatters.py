"""
Telegram message formatters — clean, rich, easy-to-read fleet reports.
Uses HTML parse mode.  Multi-company aware.  Role-aware help menus.
"""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

_TZ_ET = ZoneInfo("America/New_York")
_TZ_CT = ZoneInfo("America/Chicago")
_TZ_MT = ZoneInfo("America/Denver")
_TZ_PT = ZoneInfo("America/Los_Angeles")

from samsara_client import COMPANY_DISPLAY


def _t(key: str, lang: str | None = None) -> str:
    """Lazy wrapper for bot.i18n.t() to avoid circular imports."""
    from bot.i18n import t
    return t(key, lang)


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
    return "  ".join(badges) if badges else _t('common.all_clear')


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
        status_line = f"  {_t('welcome.api_connected')}\n"
    else:
        status_line = f"  {_t('welcome.api_not_connected')}\n"

    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('welcome.title')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"{_t('welcome.subtitle')}\n"
        f"{role_line}{acct_line}{company_line}"
        "\n"
        f"{status_line}"
        "\n"
        f"{_t('welcome.tap_begin')}\n"
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
        no_device = f"\n  ⚠️  <i>{_t('truck.no_device')}</i>"

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
        lines.append(f"  ✅  <b>{_t('truck.no_faults')}</b>")
        lines.append(f"  {_t('truck.no_faults_note')}")
    else:
        lines.append(f"  ── ── ── ── ── ── ──")
        lines.append(f"  🔧  <b>{len(dtcs)} {_t('truck.active_faults')}</b>")

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
                f"\n  {sev}  <b>{_t('truck.fault_label')} #{i}</b>\n"
                f"     {_t('truck.code_label')}    SPN {spn} / FMI {fmi}\n"
                f"     {_t('truck.issue_label')}   {spn_desc}\n"
                f"     {_t('truck.level_label')}   {fmi_desc}\n"
                f"     {_t('truck.count_label')}   ×{occ}\n"
                f"     {_t('truck.from_label')}    {source}"
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
        f"  🔍  <b>#{truck_name}</b>  {_t('truck.found_in')}",
        f"       {len(matches)} {_t('common.companies')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('truck.select_which')}",
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
            f"  {_t('alert_format.fuel_ok_title')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"\n  {_t('alert_format.fuel_ok_msg').replace('{pct}', str(threshold))}"
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('alert_format.low_fuel_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {len(low_fuel_vehicles)} {_t('common.trucks')} < {threshold}%",
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
        f"  {_t('alert_format.new_fault_title')}",
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
            f"       {_t('alert_format.new_fault_source')} {source}\n"
        )

    lines.append(f"  {_t('alert_format.new_fault_hint')}")
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
        f"  {_t('alert_format.critical_fault_title')}",
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
            f"       {_t('alert_format.new_fault_source')} {source}\n"
        )

    lines.append(f"  {_t('alert_format.critical_fault_action')}")
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
    header = _t('alert_format.health_critical') if is_critical else _t('alert_format.health_warning')
    icon = "🛑" if is_critical else "⚠️"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {icon}  {header}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  🚛  <b>Truck #{name}</b>",
        f"{co_label}",
        "",
    ]

    # Show each alert condition with current value
    alert_details = {
        "low_battery": (
            "🔋", _t('alert_format.health_low_battery'),
            f"{health.get('battery_v', '?')}V (threshold: 12.2V)",
        ),
        "low_oil_pressure": (
            "🛢", _t('alert_format.health_low_oil'),
            f"{health.get('oil_psi', '?')} PSI (threshold: 10 PSI)",
        ),
        "high_coolant_temp": (
            "🌡", _t('alert_format.health_high_coolant'),
            f"{health.get('coolant_c', '?')}°C (threshold: 105°C)",
        ),
        "low_def": (
            "💧", _t('alert_format.health_low_def'),
            f"{health.get('def_pct', '?')}% (threshold: 10%)",
        ),
        "coolant_dtc": (
            "🌡", _t('alert_format.health_coolant_fault'),
            _t('alert_format.health_coolant_dtc'),
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
        f"  {_t('alert_format.low_fuel_push_title')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  🚛  <b>Truck #{name}</b>\n"
        f"{co_label}\n"
        "\n"
        f"  {_t('alert_format.fuel_level').replace('{pct}', f'{fuel_pct:.0f}')}\n"
        "\n"
        f"  {_t('alert_format.low_fuel_refuel')}"
    )


def _fmt_us_times(iso_str: str) -> str:
    """Convert an ISO timestamp to a compact multi-zone US display.

    Returns e.g. '03-20 06:01 ET / 05:01 CT / 04:01 MT / 03:01 PT'
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        et = dt.astimezone(_TZ_ET)
        ct = dt.astimezone(_TZ_CT)
        mt = dt.astimezone(_TZ_MT)
        pt = dt.astimezone(_TZ_PT)
        # Date from ET, then times for each zone
        date_prefix = et.strftime("%m-%d")
        return (
            f"{date_prefix} {et.strftime('%I:%M%p')} ET / "
            f"{ct.strftime('%I:%M%p')} CT / "
            f"{mt.strftime('%I:%M%p')} MT / "
            f"{pt.strftime('%I:%M%p')} PT"
        )
    except Exception:
        return iso_str[:16].replace("T", " ") if len(iso_str) > 16 else iso_str


def format_alert_history_footer(occurrence_count: int,
                                first_seen: str,
                                last_seen: str) -> str:
    """Format a history footer for consolidated alerts.

    Shows occurrence count and time range when count > 1.
    Timestamps displayed in all 4 major US time zones.
    """
    if occurrence_count <= 1:
        return ""

    first_display = _fmt_us_times(first_seen)
    last_display = _fmt_us_times(last_seen)

    return (
        "\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('alert_format.history_footer_title')}\n"
        f"  {_t('alert_format.history_occurrences').replace('{count}', str(occurrence_count))}\n"
        f"  {_t('alert_format.history_since').replace('{date}', first_display)}\n"
        f"  {_t('alert_format.history_latest').replace('{date}', last_display)}\n"
    )


# ═══════════════════════════════════════════════════════════════════
#  Registration / Account Management Formatters
# ═══════════════════════════════════════════════════════════════════

def format_welcome_unregistered(support_contact: str = "", name: str = "") -> str:
    """Shown to users who haven't registered or joined yet."""
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('welcome_unreg.title')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"{_t('welcome_unreg.subtitle')}\n"
        "\n"
        f"{_t('welcome_unreg.reports_title')}\n"
        "  · Fault codes &amp; diagnostics\n"
        "  · Fuel levels &amp; consumption\n"
        "  · Vehicle health monitoring\n"
        "\n"
        f"{_t('welcome_unreg.tools_title')}\n"
        "  · Driver safety scorecards\n"
        "  · Live GPS location &amp; maps\n"
        "  · Route history &amp; geofences\n"
        "\n"
        f"{_t('welcome_unreg.costs_title')}\n"
        "  · Fuel cost tracking\n"
        "  · Cost-per-mile analysis\n"
        "  · Maintenance records\n"
        "\n"
        f"{_t('welcome_unreg.ai_title')}\n"
        "  · Ask anything about your fleet\n"
        "  · Instant insights &amp; summaries\n"
        "  · Smart follow-up suggestions\n"
        "\n"
        f"{_t('welcome_unreg.alerts_title')}\n"
        "  · Real-time fault alerts\n"
        "  · Scheduled PDF report delivery\n"
        "  · Geofence notifications\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('welcome_unreg.contact_admin')}\n"
        "  👉 https://t.me/Allen_Klein\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )


def format_register_success(account_name: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('register.success_title')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  🏢  <b>{account_name}</b>\n"
        f"  {_t('register.you_are_owner')}\n"
        "\n"
        f"  {_t('register.next_steps')}\n"
        f"  {_t('register.step_add_company')}\n"
        "       to connect your Company\n"
        "\n"
        f"  {_t('register.step_invite')}\n"
        "       to invite team members\n"
    )


def format_join_success(account_name: str, role_str: str) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('join.success_title').replace('{account}', account_name.upper())}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {_t('join.role_label').replace('{role}', role_str)}\n"
        "\n"
        f"  {_t('join.tap_begin')}\n"
    )


def format_account_info(account, companies, user) -> str:
    """Companies overview for the Companies sub-menu."""
    from permissions import role_display
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('company.header').replace('{account}', account.name)}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('company.your_role').replace('{role}', role_display(user.role))}",
        "",
        f"  📡  <b>{len(companies)}</b> Samsara {_t('common.companies') if len(companies) != 1 else _t('common.company_singular')}",
    ]
    for co in companies:
        lines.append(f"     • {co.code} — {co.display_name or co.code}")

    return "\n".join(lines)


def format_invite_created(code: str, role_str: str, dept: str,
                         invite_link: str | None = None) -> str:
    text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('invite.created_title')}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {_t('invite.code_label')}  <code>{code}</code>\n"
        f"  {_t('invite.role_label')}  {role_str}\n"
        f"  {_t('invite.dept_label')}  {dept}\n"
        f"  {_t('invite.expires')}\n"
    )
    if invite_link:
        text += (
            "\n"
            f"  {_t('invite.share_label')}\n"
            f"  {invite_link}\n"
            "\n"
            f"  {_t('invite.share_note')}\n"
        )
    else:
        text += (
            "\n"
            f"  {_t('invite.share_instructions')}\n"
            "\n"
            f"  {_t('invite.share_step1')}\n"
            f"  {_t('invite.share_step2')}\n"
            f"  {_t('invite.share_step3')} <code>{code}</code>\n"
        )
    return text


def format_users_list(users, account_name: str) -> str:
    """List all users for /users command."""
    from permissions import role_display
    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('team.header').replace('{account}', account_name)}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for u in users:
        alerts = "🔔" if u.alerts_on else "🔕"
        truck = f"  ·  Truck #{u.truck_num}" if u.truck_num else ""
        alerts_label = _t('team.alerts_on') if u.alerts_on else _t('team.alerts_off')
        lines.append(
            f"  {role_display(u.role)}\n"
            f"     {u.linked_label}  ·  {u.department}{truck}\n"
            f"     {alerts}  {alerts_label}\n"
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
        truck_info += f"\n  {_t('company.trucks_total').replace('{count}', str(total_trucks))}"
        if active_trucks is not None:
            truck_info += (
                f"\n  {_t('company.trucks_active').replace('{count}', str(active_trucks))}"
            )
    return (
        "━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('company.added_title')}\n"
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
        f"  {_t('admin.sysowner_title')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {_t('admin.sysowner_msg')}\n"
        f"  {_t('admin.sysowner_manage')}\n"
    )


def format_admin_dashboard(stats: dict) -> str:
    """System owner admin dashboard with bot-wide analytics."""
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━",
        f"  {_t('admin.dashboard_title')}",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('admin.platform_overview')}",
        "",
        f"  {_t('admin.accounts_count').replace('{count}', str(stats['active_accounts']))}"
        f"  ({stats['inactive_accounts']} disabled)",
        f"  {_t('admin.users_count').replace('{count}', str(stats['active_users']))}"
        f"  (of {stats['total_users']} total)",
        f"  {_t('admin.samsara_count').replace('{count}', str(stats['active_companies']))}"
        f"  (of {stats['total_companies']} total)",
        f"  {_t('admin.alerts_count').replace('{count}', str(stats['alert_subscribers']))}",
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
            f"  {_t('admin.no_accounts_title')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('admin.no_accounts_msg')}"
        )

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('admin.all_accounts_title').replace('{count}', str(len(accounts)))}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for acct in accounts:
        status = "🟢" if acct.is_active else "🔴"
        lines.append(
            f"  {status} 🏢  <b>{acct.name}</b>  (#{acct.id})\n"
            f"      Since {acct.created_at[:10]}\n"
        )

    lines.append(f"  {_t('admin.tap_for_details')}")

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
            f"  {_t('reports.efficiency_fmt_none')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('reports.efficiency_fmt_no_data').replace('{days}', str(days))}"
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
        f"  {_t('reports.efficiency_fmt_title')}",
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
            f"  {_t('reports.health_fmt_none')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('reports.health_fmt_no_data')}"
        ]

    alert_count = sum(len(v.get("_health_alerts", [])) for v in vehicles)
    crit_count = sum(1 for v in vehicles if v.get("_health_alerts"))
    eng_on = sum(1 for v in vehicles if v.get("_health", {}).get("engine_on"))
    eng_off = len(vehicles) - eng_on

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('reports.health_fmt_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('reports.health_scanned').replace('{count}', str(len(vehicles)))}",
        f"  {_t('reports.health_engine_on').replace('{on}', str(eng_on)).replace('{off}', str(eng_off))}",
        f"  {_t('reports.health_alerts_summary').replace('{crit}', str(crit_count)).replace('{total}', str(alert_count))}",
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

        detail = "  ·  ".join(parts) if parts else _t('common.no_data')

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
            f"  {_t('reports.weather_none')}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  {_t('reports.weather_none_msg')}"
        ]

    temps = [v["_weather"]["temp_f"] for v in vehicles
             if v.get("_weather", {}).get("temp_f") is not None]
    freezing = sum(1 for t in temps if t <= 32)
    hot = sum(1 for t in temps if t >= 100)
    avg_temp = sum(temps) / len(temps) if temps else 0

    lines = [
        "━━━━━━━━━━━━━━━━━━━",
        f"  {_t('reports.weather_title')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        f"  {_t('reports.weather_reporting').replace('{count}', str(len(vehicles)))}",
        f"  {_t('reports.weather_avg').replace('{temp}', f'{avg_temp:.0f}')}",
    ]
    if freezing:
        lines.append(f"  {_t('reports.weather_freezing').replace('{count}', str(freezing))}")
    if hot:
        lines.append(f"  {_t('reports.weather_hot').replace('{count}', str(hot))}")
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
#  EVENTS
# ═══════════════════════════════════════════════════════════════════

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


def format_event_alert(event: dict) -> str:
    """Format a single event for push notification (HTML)."""
    etype = event.get("event_type", "unknown")
    emoji = _EVENT_EMOJI.get(etype, "🚨")
    ename = event.get("event_name", "Event")
    vname = event.get("vehicle_name", "?")
    dname = event.get("driver_name", "Unassigned")
    gf = event.get("g_force", 0.0)
    lat = event.get("latitude")
    lng = event.get("longitude")
    time_str = _fmt_time(event.get("time", ""))

    loc_str = f"{lat:.4f}, {lng:.4f}" if lat is not None and lng is not None else "—"

    return (
        f"{emoji} <b>{ename}</b>\n\n"
        f"  🚛 {_t('events.vehicle_label')}: <b>{vname}</b>\n"
        f"  👤 {_t('events.driver_label')}: <b>{dname}</b>\n"
        f"  ⚡ {_t('events.gforce_label')}: <b>{gf:.2f}g</b>\n"
        f"  📍 {_t('events.location_label')}: {loc_str}\n"
        f"  🕐 {_t('events.time_label')}: {time_str}\n"
    )


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
    from collections import Counter
    type_counts = Counter(e.get("event_type", "unknown") for e in events)
    type_order = ["crash", "braking", "rollingStop", "followingDistance",
                  "harshTurn", "laneDeparture", "acceleration"]

    summary_lines = [f"\n  {_t('events.summary_header')}"]
    for etype in type_order:
        cnt = type_counts.get(etype, 0)
        if cnt > 0:
            emoji = _EVENT_EMOJI.get(etype, "🚨")
            key = _EVENT_TYPE_KEYS.get(etype, "events.type_crash")
            summary_lines.append(f"  {_t(key)}: {cnt}")
    summary_lines.append(f"  ─────────────")
    summary_lines.append(f"  {_t('events.total').format(count=len(events))}")

    # Top 5 drivers by event count
    driver_counts = Counter(e.get("driver_name", "Unassigned") for e in events)
    top_5 = driver_counts.most_common(5)
    driver_lines = [f"\n  {_t('events.top_drivers')}"]
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
            display = COMPANY_DISPLAY.get(org, org)
            company_lines.append(f"  {_t('events.company_line').format(company=display, count=cnt)}")

    full = "\n".join([header] + summary_lines + driver_lines + gforce_lines + company_lines)
    return _split_message(full)

