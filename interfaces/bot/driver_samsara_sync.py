"""Daily Driver/Samsara identity-sync job — bot-side wrapper.

Pure detection logic lives in
:mod:`capabilities.drivers.samsara_sync`.  This module fetches the
inputs, runs the detector per account, and sends a single digest
message to each account's admins (Owner + Admin roles).  Soft-fails
when Samsara is unreachable so the job doesn't block other workloads.
"""

from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.ext import Application

from adapters.storage import Role
from capabilities.drivers.samsara_sync import (
    detect_mismatches, format_digest,
)
from capabilities.localization.tz import is_local_hour
from infra.bot_registry import get_app_for_account
from infra.isolation import run_account_job
from infra.services import get_client as _get_samsara, get_tenant_db as _get_tenant_db_rls
from interfaces.bot.state import get_platform_db

# Fires at 10:00 LOCAL for each account — one hour after the doc-expiry
# digest so admins get the two driver pings back-to-back.
_TARGET_HOUR_LOCAL = 10

logger = logging.getLogger(__name__)


async def check_driver_samsara_sync(_app: Application | None = None) -> None:
    """Run identity-drift detection for every account.

    Runs once per day (registered at 10:00 UTC, after the document
    expiration job at 09:00 so the two daily admin pings don't collide
    on the second).  Pure-additive: if no mismatches are found for an
    account, no message goes out.
    """
    try:
        pdb = get_platform_db()
        accounts = await pdb.list_accounts()
    except Exception as e:
        logger.error("Driver/Samsara sync — cannot list accounts: %s", e, exc_info=True)
        return

    total_sent = 0
    total_mismatches = 0

    for account in accounts:
        async def _run(acct=account):
            nonlocal total_sent, total_mismatches
            # Per-account local-hour gate — skip 23 of the 24 daily ticks.
            if not await is_local_hour(acct.id, _TARGET_HOUR_LOCAL):
                return
            bot_app = get_app_for_account(acct.id)
            if bot_app is None:
                return

            local_drivers = await pdb.list_drivers(acct.id, include_terminated=False)
            if not local_drivers:
                return

            # Samsara data — best-effort.  When unreachable we still
            # exit cleanly (no false alerts).
            try:
                samsara = await _get_samsara(acct.id)
                samsara_drivers = await samsara.get_drivers()
            except Exception as e:
                logger.debug(
                    "Samsara unavailable for account %s sync: %s", acct.id, e,
                )
                return

            # Merge static assignments across every company in the
            # account (each SamsaraClient returns its own map).
            static_assignments: dict[str, str] = {}
            for code, single in samsara.clients.items():
                try:
                    static_assignments.update(
                        await single.get_static_driver_assignments()
                    )
                except Exception:
                    # Some accounts don't have the decoration param —
                    # treat as "no assignments available for this co".
                    continue

            mismatches = detect_mismatches(
                local_drivers, samsara_drivers, static_assignments,
            )
            if not mismatches:
                return
            total_mismatches += len(mismatches)

            text = format_digest(mismatches)

            # Forum routing → Sync & System topic.  When configured the
            # whole admin team sees the digest in one place instead of
            # each admin getting an identical DM.
            from capabilities.alerting.pipeline import post_alert_to_topic
            posted = await post_alert_to_topic(
                bot_app, account_id=acct.id,
                alert_type="samsara_sync", text=text,
                severity="info",
            )
            if posted:
                total_sent += 1
            else:
                users = await pdb.list_account_users(acct.id)
                for u in users:
                    if u.role not in (Role.OWNER, Role.ADMIN):
                        continue
                    if not u.telegram_id:
                        continue
                    try:
                        await bot_app.bot.send_message(
                            chat_id=u.telegram_id, text=text,
                            parse_mode=ParseMode.HTML,
                        )
                        total_sent += 1
                    except Exception as e:
                        logger.debug(
                            "Sync digest to admin %s failed: %s", u.telegram_id, e,
                        )

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(
            _run(), account_id=account.id,
            job_name="driver_samsara_sync",
            tenant_db=tenant_db_rls,
        )

    if total_sent or total_mismatches:
        logger.info(
            "Driver/Samsara sync — mismatches=%d digests_sent=%d",
            total_mismatches, total_sent,
        )
