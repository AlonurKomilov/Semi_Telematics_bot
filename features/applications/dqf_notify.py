"""Who hears about the DQF passphrase, and when.

Two triggers, both to the ACCOUNT'S OWNERS rather than to whoever acted:

  * the passphrase changed — carries the new value, so a copy exists
    somewhere the carrier controls before anything goes wrong;
  * exports started including the SSN file — carries no value, just the
    fact and where to set a stronger passphrase.

Owners, not the actor, because someone with delegated
``can_manage_config_all`` may set this and then leave the company, taking
the only external copy with them.  Owners are the durable address, and
they are the people who should learn that a delegate changed what
protects every applicant's SSN.

Mirrors ``_notify_account_contacts`` in interfaces/api/routes/system.py —
same recipient rule (every owner plus the billing contact, deduplicated),
same best-effort contract.  Kept here rather than imported because that
one lives in an interface-layer route and a feature must not reach into
interfaces/ (docs/FEATURES.md).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _contacts(platform_db, account_id: int) -> set[str]:
    """Every owner's email, plus the billing contact.  Deduplicated."""
    targets: set[str] = set()
    try:
        cur = await platform_db._db.execute(
            "SELECT email FROM users "
            "WHERE account_id = ? AND role = 'owner' AND email IS NOT NULL",
            (account_id,),
        )
        targets.update(
            dict(r)["email"] for r in await cur.fetchall() if dict(r).get("email")
        )
        sub = await platform_db.get_subscription(account_id)
        if sub and sub.get("billing_email"):
            targets.add(sub["billing_email"])
    except Exception:
        logger.exception("DQF notify: recipient lookup failed acct=%s", account_id)
    return targets


async def notify_passphrase_changed(
    platform_db, account_id: int, *, passphrase: str, changed_by: str = "",
) -> int:
    """Mail the new passphrase to the account's owners.  Returns sends.

    Best-effort by contract: a mail failure must never abort the save
    that triggered it.  The passphrase is already stored; a missing email
    costs a copy, not the setting.
    """
    from capabilities.email.application_emails import send_dqf_passphrase_email

    account_name = ""
    try:
        acct = await platform_db.get_account(account_id)
        account_name = getattr(acct, "name", "") or ""
    except Exception:
        pass

    sent = 0
    for addr in await _contacts(platform_db, account_id):
        try:
            if send_dqf_passphrase_email(
                to=addr, account_name=account_name,
                passphrase=passphrase, changed_by=changed_by,
            ):
                sent += 1
        except Exception:
            logger.exception("DQF passphrase email failed for %s", addr)
    return sent


async def notify_export_started(platform_db, tenant_db, account_id: int) -> bool:
    """One-time notice that exports now carry the SSN file.

    Guarded by an account setting so it fires ONCE.  The flag is written
    BEFORE the mail goes out: a duplicate notice is a nuisance, but a
    crash between send and flag would re-send on every export, which
    turns a courtesy into noise the account learns to ignore.
    """
    from capabilities.email.application_emails import send_dqf_export_started_email

    KEY = "dqf.export_notice_sent"
    try:
        if await tenant_db.get_account_setting(account_id, KEY, ""):
            return False
        await tenant_db.set_account_setting(account_id, KEY, "1")
    except Exception:
        logger.exception("DQF export notice: flag check failed acct=%s", account_id)
        return False

    account_name = ""
    try:
        acct = await platform_db.get_account(account_id)
        account_name = getattr(acct, "name", "") or ""
    except Exception:
        pass

    ok = False
    for addr in await _contacts(platform_db, account_id):
        try:
            ok = send_dqf_export_started_email(
                to=addr, account_name=account_name,
            ) or ok
        except Exception:
            logger.exception("DQF export notice failed for %s", addr)
    return ok
