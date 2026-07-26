"""Bot-side wiring for the driver-document expiration scheduler.

The pure logic — bucketing, message formatting, expired-flip detection —
lives in :mod:`features.drivers.expiration`.  This module is the
thin adapter: it iterates accounts, calls the platform DB, sends
Telegram messages, records dedup rows, and logs counters.
"""

from __future__ import annotations

import logging

from telegram.constants import ParseMode
from telegram.ext import Application

from adapters.storage import Role
from features.drivers.expiration import (
    classify_documents, format_alert,
)
from capabilities.localization.tz import is_local_hour
from infra.bot_registry import get_app_for_account
from infra.isolation import run_account_job
from infra.services import get_tenant_db as _get_tenant_db_rls
from interfaces.bot.state import get_platform_db
from capabilities.alerting.registry import register_alert_source

# Per-account local hour at which this job's per-account body runs.
# Scheduler ticks hourly; the gate below skips the other 23 ticks per
# account so each account only sees one delivery per day, at its own
# local 09:00 (e.g. Eastern fleet 14:00 UTC, Pacific fleet 17:00 UTC).
_TARGET_HOUR_LOCAL = 9

logger = logging.getLogger(__name__)


@register_alert_source("driver_doc_expiry_check", trigger="cron", minute=2)
async def check_document_expirations(_app: Application | None = None) -> None:
    """Daily — fire driver-document expiration alerts and mark past-due
    documents as ``expired``.

    Cadence: scheduler ticks hourly; each per-account body fires only
    when it's currently 09:00 in that account's effective timezone.
    Dedup is per-(doc_id, bucket_days), so re-running the same day is
    a no-op even when an account spans timezones.
    """
    try:
        pdb = get_platform_db()
        accounts = await pdb.list_accounts()
    except Exception as e:
        logger.error("Document-expiry check — cannot list accounts: %s", e, exc_info=True)
        return

    total_alerts = 0
    total_expired = 0

    for account in accounts:
        async def _run(acct=account):
            nonlocal total_alerts, total_expired
            # Per-account local-hour gate — skip 23 of the 24 daily ticks.
            if not await is_local_hour(acct.id, _TARGET_HOUR_LOCAL):
                return
            bot_app = get_app_for_account(acct.id)
            if bot_app is None:
                return

            # Pull every active doc expiring within the largest bucket
            # (30 days) — the classifier picks the right per-doc bucket.
            docs = await pdb.get_expiring_documents(acct.id, within_days=30)
            if not docs:
                return
            alerts, expired = classify_documents(docs)

            # 1) Flip past-due docs to status='expired'.
            for d in expired:
                try:
                    await pdb.mark_document_status(d.id, "expired")
                    total_expired += 1
                except Exception as e:
                    logger.debug("Could not flip doc %s to expired: %s", d.id, e)

            if not alerts:
                return

            # 2) Cache account-admin telegram_ids once per account.
            try:
                users = await pdb.list_account_users(acct.id)
            except Exception as e:
                logger.debug("Could not list users for account %s: %s", acct.id, e)
                return
            admin_chat_ids = [
                u.telegram_id for u in users
                if u.role in (Role.OWNER, Role.ADMIN) and u.telegram_id
            ]
            by_user_id = {u.id: u for u in users}

            # Tenant handle for the per-driver DND queue check below.
            # Fetched once per account so the loop doesn't re-resolve
            # the pool on every alert.
            from interfaces.bot.state import get_tenant_db as _get_tenant_db
            doc_tenant = await _get_tenant_db(acct.id)

            # 3) For each alert: record dedup row first, then send.
            for a in alerts:
                fresh = await pdb.record_doc_notification(a.doc_id, a.bucket)
                if not fresh:
                    continue  # already alerted this bucket

                driver = by_user_id.get(a.user_id)
                if driver is None:
                    continue
                driver_name = driver.display_name or f"Driver #{driver.id}"

                driver_text = format_alert(a, for_driver=True)
                admin_text = (
                    f"👤 <b>{driver_name}</b>\n\n"
                    + format_alert(a, for_driver=False)
                )

                # Send to the driver themselves.  DND-gated: CDL /
                # medical / DQF expiry is a compliance reminder, not
                # a safety-critical alert, so respect the driver's
                # "Don't disturb me off-shift" toggle.  Critical alerts
                # (severity-tagged via the main pipeline) bypass this
                # path entirely.
                if driver.telegram_id:
                    from capabilities.alerting.dnd import is_user_dnd_active
                    skip_driver_dm = False
                    try:
                        # ``driver`` here is the per-account user row
                        # from ``by_user_id`` — a User dataclass with
                        # dnd_enabled + quiet_start/end populated.
                        if await is_user_dnd_active(driver, doc_tenant):
                            # Spine quiet queue — lands in the driver's
                            # off-shift summary at shift start (replaces
                            # the retired private dnd_alert_queue).
                            from capabilities.notifications.quiet_hours import (
                                defer_notification)
                            await defer_notification(
                                doc_tenant, acct.id,
                                user_id=driver.id,
                                address=str(driver.telegram_id),
                                category="alert.documents",
                                line=f"📄 Document expiring — {driver_name}",
                                severity="warning",
                            )
                            skip_driver_dm = True
                    except Exception as e:
                        logger.debug(
                            "Driver-doc DND check for %s failed: %s — sending anyway",
                            driver.telegram_id, e,
                        )
                    if not skip_driver_dm:
                        try:
                            await bot_app.bot.send_message(
                                chat_id=driver.telegram_id,
                                text=driver_text,
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception as e:
                            logger.debug(
                                "Driver-doc alert to %s failed: %s",
                                driver.telegram_id, e,
                            )

                # Forum routing for the admin view: one post to the
                # Driver Documents topic when configured.  The driver's
                # personal DM above is preserved — they get the heads-up
                # in private regardless of the team routing.
                from capabilities.alerting.pipeline import post_alert_to_topic
                posted = await post_alert_to_topic(
                    bot_app, account_id=acct.id,
                    alert_type="documents", text=admin_text,
                    severity="warning",
                    subject_id=f"drv:{driver.id}",
                    subject_name=driver_name,
                    dedup_key=str(getattr(a, "bucket", "") or ""),
                )
                if not posted:
                    # Fallback: send to each admin, skipping the driver
                    # themselves so an owner-who-is-also-a-driver doesn't
                    # get a duplicate.
                    for chat_id in admin_chat_ids:
                        if chat_id == driver.telegram_id:
                            continue
                        try:
                            await bot_app.bot.send_message(
                                chat_id=chat_id,
                                text=admin_text,
                                parse_mode=ParseMode.HTML,
                            )
                        except Exception as e:
                            logger.debug(
                                "Driver-doc admin alert to %s failed: %s",
                                chat_id, e,
                            )

                total_alerts += 1

        tenant_db_rls = await _get_tenant_db_rls(account.id)
        await run_account_job(
            _run(), account_id=account.id,
            job_name="driver_doc_expiry_check",
            tenant_db=tenant_db_rls,
        )

    if total_alerts or total_expired:
        logger.info(
            "Driver-doc expiry check — alerts=%d expired_flipped=%d",
            total_alerts, total_expired,
        )
