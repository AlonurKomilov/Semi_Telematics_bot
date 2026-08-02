"""Recruiting-link lifecycle — the warning before a link dies.

Daily scheduler job (``application_link_expiry``).  Until this existed
nothing watched the gap: a link lapsed silently, the recruiter found out
when an applicant reported a dead URL, and anyone part-way through the
form at that moment lost everything they had entered.

One pass, one-shot per link: every manager who can act on it is warned
once, inside ``WARN_BEFORE_DAYS`` of expiry, while extending it is still
useful.  Idempotence comes from ``application_links.expiry_warned_at``,
stamped only after a successful send — so an SMTP outage retries next run
and a scheduler retry cannot double-send.

Mirrors features/carrier_directory/intake_lifecycle.py, which does the
same job for carrier self-fill links.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Warn while chasing is still possible, not at the funeral.
WARN_BEFORE_DAYS = 3
_BATCH_LIMIT = 200   # per run — bounds a pathological backlog


async def job_application_link_expiry(_app=None) -> None:
    from infra.platform import get_platform_db
    from adapters.storage import Role
    from capabilities.permissions.roles import get_user_permissions
    from capabilities.email.application_emails import send_link_expiring_email
    from features.applications.service import apply_base_url, review_base_url

    db = get_platform_db()
    now = datetime.now(timezone.utc)
    try:
        soon = await db.list_links_expiring_soon(
            now_iso=now.isoformat(),
            before_iso=(now + timedelta(days=WARN_BEFORE_DAYS)).isoformat(),
        )
    except Exception:
        logger.exception("application link expiry: query failed")
        return
    if not soon:
        return

    apply_base = apply_base_url()
    manage_url = f"{review_base_url()}/workforce/applications"
    for row in soon[:_BATCH_LIMIT]:
        delivered = False
        try:
            # The token is an edit credential and this row travels through
            # logs and exception paths, so it is fetched at send time only.
            link = await db.get_application_link_by_id(row["id"])
            token = (link or {}).get("token") or ""
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
                    if not perms.can_manage_applications:
                        continue
                    if send_link_expiring_email(
                        to=email,
                        link_label=row.get("label") or "",
                        days_left=_days_until(row.get("expires_at"), now),
                        apply_url=f"{apply_base}/{token}" if token else apply_base,
                        manage_url=manage_url,
                    ):
                        delivered = True
                except Exception:
                    logger.debug("link expiry warn skipped one user")
            # Stamp once the link has been announced to SOMEONE — otherwise a
            # single bad address replays the whole fan-out every night.
            if delivered:
                await db.mark_link_expiry_warned(row["id"])
        except Exception:
            logger.exception("application link expiry failed for link %s", row.get("id"))


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
