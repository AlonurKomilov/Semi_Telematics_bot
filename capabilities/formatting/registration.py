"""Registration, onboarding, and invite formatters."""

from infra.context import get_company_display
from capabilities.formatting.helpers import _t


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
        from capabilities.iam.permissions import role_display
        role_line = f"\n  {role_display(user.role)}  ·  {account.name}\n"
    elif account:
        acct_line = f"\n  🏢 {account.name}\n"

    company_line = ""
    has_api = bool(company_codes)
    # Only show the multi-company block to roles that actually span
    # multiple companies.  Drivers are scoped to a single truck and
    # listing every company in the account just leaks fleet structure.
    is_driver = False
    if user is not None:
        try:
            is_driver = getattr(user.role, "value", str(user.role)) == "driver"
        except Exception:  # pragma: no cover — defensive
            is_driver = False
    if not is_driver and company_codes and len(company_codes) > 1:
        names = [f"{c} ({get_company_display().get(c, c)})" for c in company_codes]
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


def format_unregistered_member(
    account_name: str,
    name: str = "",
    support_contact: str = "@Allen_Klein",
) -> str:
    """Welcome shown to a Telegram user who started a per-account bot
    but isn't yet a registered member of that account.

    Role-agnostic copy: 4truck supports drivers, dispatchers, safety,
    fleet, and admin/owner roles, so the platform tagline and the
    feature list are intentionally written without singling out any
    one role.  Used by:
        - interfaces/bot/registration.py  (the long /start path)
        - interfaces/bot/auth.py          (short /start auth check)
        - interfaces/bot/callbacks/__init__.py  (callback re-render)
    Centralised here so tagline edits land everywhere at once.
    """
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  👋 Hi{(' ' + name) if name else ''}!\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"This bot belongs to <b>{account_name}</b>.\n"
        "\n"
        "You're not registered as a member yet.\n"
        "Ask your manager or admin to add you to\n"
        "the team — they can send you an invite link.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  🚛 <b>4truck — Logistics Operations Platform</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "Built for drivers, dispatchers, safety,\n"
        "fleet, and admins — live tracking, fault\n"
        "diagnostics, AI insights, driver scorecards,\n"
        "payroll, coaching, and more.\n"
        "\n"
        "  🌐 <a href=\"https://4truck.us\">4truck.us</a>\n"
        f"  📩 Contact: {support_contact}\n"
    )


def format_welcome_unregistered(support_contact: str = "", name: str = "") -> str:  # noqa: ARG001
    """Shown to users who haven't registered or joined yet.

    The feature list is intentionally narrowed to what the *bot*
    actually does day to day — the dashboard owns management,
    reports, and configuration.  Two reasons this matters:

      * Promising bot-side scorecards / maps / cost reports here
        and then redirecting on every tap reads as a bait-and-
        switch.
      * New customers should learn the split (bot for moments,
        dashboard for browsing) on first contact instead of
        discovering it the hard way.
    """
    return (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"  {_t('welcome_unreg.title')}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"{_t('welcome_unreg.subtitle')}\n"
        "\n"
        "🔔 <b>Real-time alerts</b>\n"
        "  · Critical faults &amp; health\n"
        "  · Low-fuel &amp; DEF warnings\n"
        "  · Maintenance due reminders\n"
        "  · Geofence enter / exit\n"
        "  · Tap inline to acknowledge\n"
        "\n"
        "🚛 <b>Driver self-service</b>\n"
        "  · Pre-trip inspections — /pti\n"
        "  · Paystub lookup — /my_pay\n"
        "  · Coaching tasks — /my_coaching\n"
        "  · Log a fill-up — /fuelcost\n"
        "  · Submit a shop invoice — /invoice\n"
        "\n"
        "🔍 <b>Quick lookups</b>\n"
        "  · Vehicle status — /vehicle &lt;name&gt;\n"
        "  · One-truck camera check — /cam &lt;name&gt;\n"
        "  · Knowledge base — /tips\n"
        "  · Ask the AI assistant — /ai\n"
        "\n"
        "🌐 <b>The dashboard handles the rest</b>\n"
        "  · Live map, scorecards, reports\n"
        "  · Fleet maintenance &amp; cost tracking\n"
        "  · Team / role / API key management\n"
        "  · Open at <a href=\"https://dash.4truck.us\">dash.4truck.us</a>\n"
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


def format_org_added(
    code: str,
    display_name: str,
    total_trucks: int | None = None,
    active_trucks: int | None = None,
) -> str:
    truck_info = ""
    if total_trucks is not None:
        truck_info += f"\n  {_t('company.vehicles_total').replace('{count}', str(total_trucks))}"
        if active_trucks is not None:
            truck_info += (
                f"\n  {_t('company.vehicles_active').replace('{count}', str(active_trucks))}"
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
