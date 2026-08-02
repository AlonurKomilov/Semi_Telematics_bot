"""Carrier self-fill link lifecycle — the sweep between mint and reply.

Daily scheduler job (``carrier_intake_lifecycle``).  Until this existed
there was no system at all in the gap: a carrier who put the invite aside
was never nudged, a manager was never told a link was about to lapse, and
nobody on either side learned that one had.  The link simply went quiet
and the manager found out when the carrier reported a dead URL.

Two passes, both one-shot per link:

* **Remind the carrier** once, when an invite we really emailed has gone
  unanswered for ``REMIND_AFTER_DAYS``.
* **Warn the managers** once, when a still-unfilled link falls inside
  ``WARN_BEFORE_DAYS`` of expiry — while there is still time to chase it
  or re-mint.

Idempotence comes from the marker columns (``intake_reminded_at`` /
``intake_expiry_warned_at``), stamped ONLY after a successful send, so an
SMTP outage retries next run and a scheduler retry cannot double-send.
Re-minting a link clears both, which is correct: a new link starts its own
cycle.  Mirrors features/applications/drafts_reminder.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# One nudge, a week in — long enough not to badger an office that is
# simply busy, short enough to still matter on a 30-day link.
REMIND_AFTER_DAYS = 7
# Warn the managers while chasing is still possible, not at the funeral.
WARN_BEFORE_DAYS = 3
_BATCH_LIMIT = 200   # per pass, per run — bounds a pathological backlog


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def job_carrier_intake_lifecycle(_app=None) -> None:
    from infra.platform import get_platform_db

    db = get_platform_db()
    now = datetime.now(timezone.utc)
    await _remind_carriers(db, now)
    await _warn_managers(db, now)


async def _remind_carriers(db, now: datetime) -> None:
    """One reminder per unanswered invite."""
    from features.applications.service import apply_base_url
    from features.carrier_directory.service import (
        PUBLIC_FIELD_COUNT, resolve_sender_name,
    )
    from capabilities.email.application_emails import send_carrier_intake_email

    try:
        due = await db.list_intakes_needing_reminder(
            now_iso=_iso(now),
            older_than_iso=_iso(now - timedelta(days=REMIND_AFTER_DAYS)),
        )
    except Exception:
        logger.exception("carrier intake: reminder query failed")
        return
    if not due:
        return

    base = apply_base_url()
    sent = 0
    for row in due[:_BATCH_LIMIT]:
        try:
            # Re-resolve rather than reusing the stored override directly:
            # the account-wide public name may have changed since the
            # invite went out, and the reminder must match what the page
            # will actually say.
            # Read the token at send time; if it vanished since the sweep
            # query (revoked, re-minted) there is nothing valid to link to,
            # and mailing a bare /carrier/ URL is worse than staying quiet.
            token = await _token_for(db, row["id"])
            if not token:
                continue
            acct = await db.get_account(row["account_id"])
            sender = resolve_sender_name(row, acct)
            days_left = _days_until(row.get("intake_expires_at"), now)
            ok = send_carrier_intake_email(
                to=row["intake_email"],
                carrier_name=row.get("name") or "",
                agency_name=sender,
                intake_url=f"{base}/carrier/{token}",
                expires_days=max(days_left, 1),
                field_count=PUBLIC_FIELD_COUNT,
                reminder=True,
            )
            if ok:
                await db.stamp_intake_marker(row["id"], "intake_reminded_at")
                sent += 1
        except Exception:
            logger.exception("carrier intake: reminder failed for profile %s", row.get("id"))
    if sent:
        logger.info("carrier intake: %s reminder(s) sent", sent)


async def _warn_managers(db, now: datetime) -> None:
    """One pre-expiry warning per link, to everyone who could act on it."""
    from adapters.storage import Role
    from capabilities.permissions.roles import get_user_permissions
    from capabilities.email.application_emails import (
        send_carrier_intake_expiring_email,
    )
    from features.applications.service import review_base_url

    try:
        soon = await db.list_intakes_expiring_soon(
            now_iso=_iso(now),
            before_iso=_iso(now + timedelta(days=WARN_BEFORE_DAYS)),
        )
    except Exception:
        logger.exception("carrier intake: expiry query failed")
        return
    if not soon:
        return

    base = review_base_url()
    for row in soon[:_BATCH_LIMIT]:
        delivered = False
        try:
            users = await db.list_account_users(row["account_id"])
            for u in users:
                email = getattr(u, "email", None)
                if not email:
                    continue
                try:
                    perms = await get_user_permissions(
                        Role(getattr(u.role, "value", u.role)), row["account_id"],
                        is_manager=bool(getattr(u, "is_manager", False)),
                        is_primary_owner=bool(getattr(u, "is_primary_owner", False)),
                    )
                    if not perms.can_manage_carrier_directory:
                        continue
                    if send_carrier_intake_expiring_email(
                        to=email,
                        carrier_name=row.get("name") or "",
                        days_left=_days_until(row.get("intake_expires_at"), now),
                        contact_email=row.get("intake_email") or "",
                        profile_url=f"{base}/workforce/carrier-directory/{row['id']}",
                    ):
                        delivered = True
                except Exception:
                    logger.debug("carrier intake: expiry warn skipped one user")
            # Stamp once the link has been announced to SOMEONE — otherwise
            # a single bad address would replay the whole fan-out nightly.
            if delivered:
                await db.stamp_intake_marker(row["id"], "intake_expiry_warned_at")
        except Exception:
            logger.exception("carrier intake: expiry warn failed for profile %s", row.get("id"))


def _days_until(iso: str | None, now: datetime) -> int:
    if not iso:
        return 0
    try:
        end = datetime.fromisoformat(str(iso))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(int((end - now).total_seconds() // 86400), 0)
    except Exception:
        return 0


async def _token_for(db, profile_id: int) -> str:
    """The live token, read at send time.

    Not carried in the sweep query on purpose: it is an edit credential,
    and the query result travels through logs and exception paths.
    """
    row = await db.get_carrier_profile_by_id(profile_id)
    return (row or {}).get("intake_token") or ""
