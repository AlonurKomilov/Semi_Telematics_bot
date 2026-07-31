"""Stale-approval nudge — the onboarding sub-feature's hub contribution.

An applicant the recruiter approved but nobody onboarded is the failure
mode this split creates: approving and hiring are now two people's jobs,
so the handover can silently stall.  This is the contribution that makes
Onboarding a sub-feature rather than a flag on a page — it participates
in the Alerting hub like ``features/drivers/documents/alert.py``.

Cadence mirrors the sibling: the scheduler ticks hourly and each
per-account body fires only at 09:00 in that account's own timezone.
"""

from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.ext import Application

from adapters.storage import Role
from capabilities.alerting.registry import register_alert_source
from capabilities.localization.tz import is_local_hour
from infra.bot_registry import get_app_for_account
from infra.isolation import run_account_job
from interfaces.bot.state import get_platform_db

logger = logging.getLogger(__name__)

# Local hour the nudge lands (the sibling doc-expiry job uses 09:00 too).
_TARGET_HOUR_LOCAL = 9
# How long an approved applicant may sit before it's worth a nudge.  Short
# enough that a candidate doesn't cool off, long enough that a same-week
# handover is never nagged.
_STALE_DAYS = 3


@register_alert_source("driver_onboarding_stale_check", trigger="cron", minute=7)
async def check_stale_approvals(_app: Application | None = None) -> None:
    """Nudge the people who onboard when approvals are waiting."""
    try:
        pdb = get_platform_db()
        accounts = await pdb.list_accounts()
    except Exception as e:
        logger.error("Onboarding nudge — cannot list accounts: %s", e, exc_info=True)
        return

    for account in accounts:
        async def _run(acct=account):
            if not await is_local_hour(acct.id, _TARGET_HOUR_LOCAL):
                return
            bot_app = get_app_for_account(acct.id)
            if bot_app is None:
                return
            try:
                waiting = await pdb.list_stale_approved_applications(
                    acct.id, older_than_days=_STALE_DAYS,
                )
            except Exception:
                logger.exception("Onboarding nudge — query failed acct=%s", acct.id)
                return
            if not waiting:
                return

            names = ", ".join(
                f"{r.get('first_name', '')} {r.get('last_name', '')}".strip()
                or r.get("reference", "?")
                for r in waiting[:5]
            )
            more = f" (+{len(waiting) - 5} more)" if len(waiting) > 5 else ""
            text = (
                f"🧑‍✈️ <b>{len(waiting)} approved applicant"
                f"{'s' if len(waiting) != 1 else ''} waiting to be onboarded</b>\n"
                f"{names}{more}\n\n"
                f"Approved more than {_STALE_DAYS} days ago and not yet hired — "
                f"open <b>Drivers → Onboarding</b> to finish."
            )
            # The people who can ACT on it: onboarding is HR/owner work, so
            # the nudge follows the grant, not the recruiting role.
            for user in await pdb.list_account_users(acct.id):
                if user.role not in (Role.OWNER, Role.ADMIN, Role.HR):
                    continue
                try:
                    await bot_app.bot.send_message(
                        chat_id=user.telegram_id, text=text,
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    logger.debug("onboarding nudge send failed uid=%s", user.telegram_id)

        await run_account_job(account.id, _run)
