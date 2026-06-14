"""System owner / admin dashboard formatters."""

from capabilities.formatting.helpers import _t


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


def _fmt_tokens(n: int) -> str:
    """Format token count: 1234567 → 1.2M, 45000 → 45K."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}K"
    return str(n)


def format_admin_dashboard(stats: dict, ext: dict | None = None,
                           server: dict | None = None) -> str:
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

    # ── Extended stats ──────────────────────────────────────
    if ext:
        # Roles breakdown
        roles = ext.get("roles", {})
        if roles:
            role_str = " · ".join(f"{r}: {c}" for r, c in sorted(roles.items()))
            lines.append(f"  👥 Roles: {role_str}")
            lines.append("")

        # AI usage
        ai_calls = ext.get("ai_total_calls", 0)
        ai_tokens = ext.get("ai_total_tokens", 0)
        if ai_calls:
            lines.append("  ── ── ── ── ── ── ── ──")
            lines.append("  🤖 <b>AI Usage</b> (all time)")
            lines.append(f"     {ai_calls} calls · {_fmt_tokens(ai_tokens)} tokens")
            lines.append("")
            # Group by account
            by_acct: dict[str, list] = {}
            for row in ext.get("ai_usage", []):
                by_acct.setdefault(row["account"], []).append(row)
            for acct_name, rows in by_acct.items():
                acct_calls = sum(r["calls"] for r in rows)
                acct_tok = sum(r["tokens"] for r in rows)
                model_str = ", ".join(
                    f"{r['model']} ({r['calls']})" for r in rows[:3]
                )
                if len(rows) > 3:
                    model_str += f" +{len(rows) - 3}"
                lines.append(
                    f"     🏢 {acct_name}: {acct_calls} calls · {_fmt_tokens(acct_tok)}"
                )
                lines.append(f"        {model_str}")
            lines.append("")

        # Alerts (7 days)
        alerts_7d = ext.get("alerts_7d", [])
        if alerts_7d:
            lines.append("  ── ── ── ── ── ── ── ──")
            lines.append("  🚨 <b>Alerts</b> (7 days)")
            total_7d = sum(a["total"] for a in alerts_7d)
            total_acked = sum(a["acked"] for a in alerts_7d)
            ack_pct = round(total_acked / total_7d * 100) if total_7d else 0
            lines.append(f"     {total_7d} total · {ack_pct}% acknowledged")
            for a in alerts_7d:
                pct = round(a["acked"] / a["total"] * 100) if a["total"] else 0
                lines.append(
                    f"     {a['type']}: {a['total']} ({pct}% acked)"
                )
            lines.append(f"     All time: {ext.get('alerts_total', '?')}")
            active = ext.get("alerts_active", 0)
            if active:
                lines.append(f"     ⚠️  {active} active (unacked)")
            lines.append("")

        # Database
        lines.append("  ── ── ── ── ── ── ── ──")
        lines.append("  💾 <b>Database</b>")
        lines.append(f"     Size: {ext.get('db_size_mb', '?')} MB")
        lines.append(f"     Audit log: {ext.get('audit_entries', '?')} entries")
        lines.append(f"     Digest subs: {ext.get('digest_subs', 0)}")
        maint = ext.get("maintenance", {})
        if maint:
            m_str = " · ".join(f"{k}: {v}" for k, v in maint.items())
            lines.append(f"     Maintenance: {m_str}")
        lines.append("")

    # ── Server info ─────────────────────────────────────────
    if server:
        lines.append("  ── ── ── ── ── ── ── ──")
        lines.append("  🖥 <b>Server</b>")
        if server.get("hostname"):
            lines.append(f"     Host: {server['hostname']}")
        if server.get("python"):
            lines.append(f"     Python: {server['python']}")
        if server.get("uptime"):
            lines.append(f"     Uptime: {server['uptime']}")
        if server.get("cpu_pct") is not None:
            lines.append(f"     CPU: {server['cpu_pct']}%")
        if server.get("mem_used") is not None:
            lines.append(
                f"     Memory: {server['mem_used']} / {server['mem_total']}"
                f"  ({server['mem_pct']}%)"
            )
        if server.get("disk_used") is not None:
            lines.append(
                f"     Disk: {server['disk_used']} / {server['disk_total']}"
                f"  ({server['disk_pct']}%)"
            )
        if server.get("bot_pid"):
            lines.append(f"     Bot PID: {server['bot_pid']}")
        if server.get("bot_mem"):
            lines.append(f"     Bot RSS: {server['bot_mem']}")
        lines.append("")

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
            from capabilities.permissions.roles import role_emoji
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
    from capabilities.permissions.roles import role_display
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
            f"        {u.linked_label}{truck}\n"
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
