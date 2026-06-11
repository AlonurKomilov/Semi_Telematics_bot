"""Account info and team management formatters."""

from capabilities.formatting.helpers import _t


def format_account_info(account, companies, user) -> str:
    """Companies overview for the Companies sub-menu."""
    from capabilities.iam.permissions import role_display
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


def format_users_list(users, account_name: str) -> str:
    """List all users for /users command."""
    from capabilities.iam.permissions import role_display
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
            f"     {u.linked_label}{truck}\n"
            f"     {alerts}  {alerts_label}\n"
        )
    return "\n".join(lines)
