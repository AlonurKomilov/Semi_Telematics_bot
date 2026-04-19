"""System owner commands — platform-wide administration."""

import os
import platform
import socket
import sys
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from formatters import (
    format_admin_dashboard,
    format_admin_accounts_list,
    format_admin_account_detail,
)

from bot.config import logger, invalidate_client, get_platform_db, get_tenant_db
from bot.keyboards import system_owner_kb
from bot.helpers import _show, _show_loading, _safe_error
from bot.auth import _require_system_owner


def _gather_server_info() -> dict:
    """Collect server metrics (CPU, memory, disk, uptime) without psutil."""
    info: dict = {}
    info["hostname"] = socket.gethostname()
    info["python"] = platform.python_version()

    # System uptime from /proc/uptime
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        td = timedelta(seconds=int(secs))
        days = td.days
        hrs, rem = divmod(td.seconds, 3600)
        mins = rem // 60
        info["uptime"] = f"{days}d {hrs}h {mins}m"
    except Exception:
        pass

    # CPU usage from /proc/stat (instant snapshot)
    try:
        load1, load5, load15 = os.getloadavg()
        ncpu = os.cpu_count() or 1
        info["cpu_pct"] = round(load1 / ncpu * 100, 1)
    except Exception:
        pass

    # Memory from /proc/meminfo
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    mem[parts[0]] = int(parts[1])  # kB
        total_kb = mem.get("MemTotal:", 0)
        avail_kb = mem.get("MemAvailable:", 0)
        used_kb = total_kb - avail_kb
        info["mem_total"] = f"{total_kb // 1024} MB"
        info["mem_used"] = f"{used_kb // 1024} MB"
        info["mem_pct"] = round(used_kb / total_kb * 100) if total_kb else 0
    except Exception:
        pass

    # Disk usage
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        info["disk_total"] = f"{total // (1024**3)} GB"
        info["disk_used"] = f"{used // (1024**3)} GB"
        info["disk_pct"] = round(used / total * 100) if total else 0
    except Exception:
        pass

    # Bot process info
    try:
        pid = os.getpid()
        info["bot_pid"] = pid
        # RSS from /proc/self/status
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    info["bot_mem"] = f"{rss_kb // 1024} MB"
                    break
    except Exception:
        pass

    return info


@_require_system_owner
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System owner dashboard — bot-wide analytics."""
    await _show_loading(update, context, "⏳ Loading admin dashboard…")
    try:
        stats = await get_platform_db().get_system_stats()
        ext = await get_platform_db().get_system_extended_stats()
        server = _gather_server_info()
        text = format_admin_dashboard(stats, ext=ext, server=server)
        await _show(update, context, [text], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Admin dashboard error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)])


@_require_system_owner
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all accounts (system owner)."""
    try:
        accounts = await get_platform_db().list_accounts(active_only=False)
        text = format_admin_accounts_list(accounts)
        await _show(update, context, [text], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Accounts list error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)])


@_require_system_owner
async def cmd_sysaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detailed info about a specific account: /sysaccount <id>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /sysaccount <b>ID</b>\n\n"
            "  Example:  /sysaccount 1"
        ], keyboard=system_owner_kb())
        return

    try:
        acct_id = int(context.args[0])
    except ValueError:
        await _show(update, context,
                    ["❌ Invalid account ID."],
                    keyboard=system_owner_kb())
        return

    account = await get_platform_db().get_account(acct_id)
    if not account:
        await _show(update, context,
                    [f"❌ Account #{acct_id} not found."],
                    keyboard=system_owner_kb())
        return

    tenant = await get_tenant_db(acct_id)
    companies = await tenant.get_account_companies(acct_id, active_only=False)
    users = await get_platform_db().list_account_users(acct_id)
    text = format_admin_account_detail(account, companies, users)
    await _show(update, context, [text], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all registered users: /broadcast <message>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:\n\n"
            "  /broadcast <b>Your message here</b>\n\n"
            "  This sends to ALL registered users\n"
            "  across ALL accounts."
        ], keyboard=system_owner_kb())
        return

    message_text = " ".join(context.args)
    broadcast = (
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "  📢  <b>SYSTEM NOTICE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"  {message_text}\n"
    )

    accounts = await get_platform_db().list_accounts()
    sent, failed = 0, 0
    for account in accounts:
        users = await get_platform_db().list_account_users(account.id)
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user.telegram_id,
                    text=broadcast,
                    parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Broadcast to {user.telegram_id}: {e}")
                failed += 1

    await _show(update, context, [
        f"✅ Broadcast sent.\n\n"
        f"  ✉️  Delivered: {sent}\n"
        f"  ❌  Failed: {failed}"
    ], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_sys_disable_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable/enable an account: /sysdisable <account_id>"""
    if not context.args:
        await _show(update, context, [
            "ℹ️  Usage:  /sysdisable <b>account_id</b>"
        ], keyboard=system_owner_kb())
        return

    try:
        acct_id = int(context.args[0])
    except ValueError:
        await _show(update, context, ["❌ Invalid account ID."],
                    keyboard=system_owner_kb())
        return

    account = await get_platform_db().get_account(acct_id)
    if not account:
        await _show(update, context, [f"❌ Account #{acct_id} not found."],
                    keyboard=system_owner_kb())
        return

    new_state = not account.is_active
    await get_platform_db().update_account(acct_id, is_active=new_state)

    # Invalidate client cache if disabling
    if not new_state:
        await invalidate_client(acct_id)

    status = "✅ ENABLED" if new_state else "🔴 DISABLED"
    await _show(update, context, [
        f"{status} account <b>{account.name}</b> (#{acct_id})"
    ], keyboard=system_owner_kb())


@_require_system_owner
async def cmd_sys_ai_stats(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           days: int = 90):
    """Comprehensive AI usage stats for system owner — all accounts."""
    await _show_loading(update, context, "⏳ Loading AI stats…")
    try:
        import ai
        from ai.registry import MODEL_PRICING, MODEL_REGISTRY
        from formatters.admin import _fmt_tokens

        stats = await get_platform_db().get_ai_usage_all_accounts(days=days)
        totals = stats["totals"]

        # Calculate total cost from bot DB
        total_cost = 0.0
        model_costs: dict[str, float] = {}
        for m, minfo in stats["by_model"].items():
            c = ai.estimate_cost(m, minfo["prompt"], minfo["reply"],
                                minfo["tokens"], minfo.get("thinking", 0))
            model_costs[m] = c
            total_cost += c

        # Query real Vertex AI cost from Cloud Monitoring
        cloud = await ai.get_vertex_ai_cloud_usage(days=days)

        lines = [
            "━━━━━━━━━━━━━━━━━━━━━",
            "  🤖 <b>AI USAGE — ALL ACCOUNTS</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"  📅 Last {days} days",
        ]

        # Show real Vertex AI cost first (this is the truth)
        if cloud:
            ct = cloud["totals"]
            lines.extend([
                "",
                "  ── <b>☁️ Real Vertex AI Usage</b> ──",
                f"  📊 Requests: {ct['requests']:,} ({ct['errors']:,} errors)",
                f"  🔤 Tokens: {_fmt_tokens(ct['input'])} in / {_fmt_tokens(ct['output'])} out",
                f"  💵 Base cost: ${ct['base_cost']:.4f}",
                f"  💰 <b>Cost +30%: ${ct['cost']:.4f}</b>",
                "",
            ])
            # Top models by cost
            top_cloud = sorted(cloud["by_model"].items(),
                               key=lambda x: x[1]["cost"], reverse=True)
            for m, info in top_cloud[:8]:
                disp = MODEL_REGISTRY.get(m, {}).get("display", m)
                err_s = f" +{info['errors']}err" if info["errors"] else ""
                lines.append(
                    f"  • {disp}: {info['requests']}{err_s}\n"
                    f"    {_fmt_tokens(info['input'])} in / "
                    f"{_fmt_tokens(info['output'])} out · ${info['cost']:.4f}"
                )
            if len(top_cloud) > 8:
                others_cost = sum(v["cost"] for _, v in top_cloud[8:])
                lines.append(f"  • +{len(top_cloud)-8} others · ${others_cost:.4f}")
            lines.append("")

        # Bot-tracked usage section
        lines.extend([
            "  ── <b>📱 Bot-Tracked Usage</b> ──",
            f"  📊 Requests: {totals['requests']}",
            f"  🔤 Tokens: {_fmt_tokens(totals['tokens'])}",
            f"     Input: {_fmt_tokens(totals['prompt'])} · Output: {_fmt_tokens(totals['reply'])}",
            f"  💰 Est. cost: ${total_cost:.4f}",
            "",
        ])

        # By request type
        if stats["by_type"]:
            lines.append("  ── <b>By Type</b> ──")
            for rt, info in sorted(stats["by_type"].items(),
                                   key=lambda x: x[1]["tokens"], reverse=True):
                lines.append(f"  • {rt}: {info['requests']} req ({_fmt_tokens(info['tokens'])})")
            lines.append("")

        # By model with cost
        if stats["by_model"]:
            lines.append("  ── <b>By Model</b> ──")
            for m, info in sorted(stats["by_model"].items(),
                                  key=lambda x: x[1]["tokens"], reverse=True):
                disp = MODEL_REGISTRY.get(m, {}).get("display", m)
                mc = model_costs.get(m, 0)
                lines.append(
                    f"  • {disp}: {info['requests']} req\n"
                    f"    {_fmt_tokens(info['prompt'])} in / "
                    f"{_fmt_tokens(info['reply'])} out · ${mc:.4f}"
                )
            lines.append("")

        # Per-account breakdown
        if stats["by_account"]:
            lines.append("  ── <b>By Account</b> ──")
            for acct, info in sorted(stats["by_account"].items(),
                                     key=lambda x: x[1]["tokens"], reverse=True):
                acct_cost = sum(
                    ai.estimate_cost(m, mi["prompt"], mi["reply"],
                                     mi["tokens"], mi.get("thinking", 0))
                    for m, mi in info["models"].items()
                )
                lines.append(
                    f"  🏢 <b>{acct}</b>: {info['requests']} req · "
                    f"{_fmt_tokens(info['tokens'])} · ${acct_cost:.4f}"
                )
                for m, mi in sorted(info["models"].items(),
                                    key=lambda x: x[1]["tokens"], reverse=True):
                    disp = MODEL_REGISTRY.get(m, {}).get("display", m)
                    lines.append(
                        f"     {disp}: {mi['requests']} req · "
                        f"{_fmt_tokens(mi['tokens'])}"
                    )
            lines.append("")

        # Daily (last 30 days)
        if stats["daily"]:
            lines.append(f"  ── <b>Daily</b> (last {min(days, 30)}d) ──")
            for d in stats["daily"][-14:]:  # Show last 14 days max
                lines.append(
                    f"  • {d['day']}: {d['requests']} req "
                    f"({_fmt_tokens(d['tokens'] or 0)})"
                )
            if len(stats["daily"]) > 14:
                lines.append(f"    ... +{len(stats['daily']) - 14} earlier days")
            lines.append("")

        # Pricing note
        if cloud:
            lines.append("  <i>☁️ = real Vertex AI Cloud Monitoring data.</i>")
            lines.append("  <i>📱 = bot-tracked user calls only.</i>")
            lines.append("  <i>Difference = health probes, retries, playground.</i>")
        else:
            lines.append("  <i>⚠️ Cloud Monitoring unavailable.</i>")
            lines.append("  <i>Cost is estimated from bot-tracked tokens.</i>")

        text = "\n".join(lines)
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📅 7 days", callback_data="sys_ai_7"),
                InlineKeyboardButton("📅 30 days", callback_data="sys_ai_30"),
                InlineKeyboardButton("📅 90 days", callback_data="sys_ai_90"),
            ],
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="sys_dashboard"),
            ],
        ])
        await _show(update, context, [text], keyboard=kb)
    except Exception as e:
        logger.error(f"AI stats error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)])


@_require_system_owner
async def cmd_sys_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed server info for system owner."""
    try:
        server = _gather_server_info()
        ext = await get_platform_db().get_system_extended_stats()
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━",
            "  🖥 <b>Server Details</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"  Host: {server.get('hostname', '?')}",
            f"  Python: {server.get('python', '?')}",
            f"  OS: {platform.platform()}",
            "",
        ]
        if server.get("uptime"):
            lines.append(f"  ⏱ Uptime: {server['uptime']}")
        if server.get("cpu_pct") is not None:
            lines.append(f"  🔧 CPU load: {server['cpu_pct']}% (1 min avg)")
        if server.get("mem_used") is not None:
            lines.append(
                f"  💿 Memory: {server['mem_used']} / {server['mem_total']}"
                f"  ({server['mem_pct']}%)"
            )
        if server.get("disk_used") is not None:
            lines.append(
                f"  💽 Disk: {server['disk_used']} / {server['disk_total']}"
                f"  ({server['disk_pct']}%)"
            )
        lines.append("")
        lines.append("  <b>Bot Process</b>")
        if server.get("bot_pid"):
            lines.append(f"  PID: {server['bot_pid']}")
        if server.get("bot_mem"):
            lines.append(f"  RSS: {server['bot_mem']}")
        lines.append("")
        lines.append("  <b>Database</b>")
        lines.append(f"  Size: {ext.get('db_size_mb', '?')} MB")
        lines.append(f"  Audit log: {ext.get('audit_entries', '?')} entries")
        await _show(update, context, ["\n".join(lines)], keyboard=system_owner_kb())
    except Exception as e:
        logger.error(f"Server info error: {e}", exc_info=True)
        await _show(update, context, [_safe_error(e)])
