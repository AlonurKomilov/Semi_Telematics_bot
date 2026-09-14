"""Telling a customer that one of their people is under closer watch.

Built now, switched OFF, by the owner's decision: the audience for
security findings is the operator for the moment, and this exists so
that turning it on later is a switch rather than a build.

**Why an owner should hear it at all.**  The case is a login taken
over — a dispatcher's password reused from a breach, a session
lifted from a shared machine.  The company did nothing wrong, the
person did nothing wrong, and the only party who can fix it quickly
(change the password, end the sessions, ask the person) is the
company itself.  A platform that noticed and said nothing has kept a
secret from the one party with standing to act on it.

**Why it fires from an operator's hand and never the detector's.**
The detector produces suspicions, and a suspicion repeated to a
customer as "we noticed something" is an accusation nobody has yet
made.  So the notice goes out when an OPERATOR puts a person under
closer watch — a decision a human made and signed in the audit trail
— and it says exactly that much: watched more closely, nothing
blocked, here is what you can do if you do not recognise it.

**Why it names a person and never a company.**  The notice exists for
the compromised-login case, which is a person.  When an operator
watches a whole account it is because the account itself is the
question — the 09-08 probe registered thirty-three of them — and
telling that account's owner they are being watched tells the probe.
A person-level standing has a specific subject who is not the owner;
that is the only shape this module will send.

**Why it does not say what was seen.**  Same rule as the quarantine
notice: an operator opens a review on a suspicion, the owner may repeat
what they read to the person, and a suspicion repeated as a finding is
how an innocent employee gets fired over our wording.  It also does
not hand a real intruder a list of what tripped.

**What it will not do even when on.**  Nothing for ``test`` accounts —
there is no customer there to tell.  Nothing when the watched person
IS the owner — that is the login most likely to be in the wrong hands,
and the message would go to those hands.  Nothing when a channel
cannot be resolved; the operator was told, and the audit row stands.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: The console switch (platform_settings) and its emergency env twin —
#: the same two-source shape the market-intel launch gate uses.  Off
#: until an operator turns it on.
SETTING_KEY = "security_owner_notice_enabled"
ENV_KEY = "SECURITY_OWNER_NOTICE_ENABLED"
_TRUTHY = ("1", "true", "TRUE", "yes")

CATEGORY = "security.closer_watch"


async def enabled(platform_db) -> bool:
    """Console switch first, env as override.  Default off."""
    if os.getenv(ENV_KEY, "").strip() in _TRUTHY:
        return True
    try:
        return await platform_db.get_platform_setting(SETTING_KEY, "0") == "1"
    except Exception:
        # A switch we cannot read is a switch that is off.  This is the
        # one place in the security module where failing CLOSED is the
        # safe direction: not sending is the status quo.
        logger.warning("owner notice: switch unreadable — treating as off",
                       exc_info=True)
        return False


def _register_category() -> None:
    from capabilities.notifications.categories import (
        NotificationCategory, TARGETED, get_category, register_category,
    )
    if get_category(CATEGORY) is not None:
        return
    register_category(NotificationCategory(
        key=CATEGORY,
        label="A team member's login is under closer watch",
        kind=TARGETED,
        # Security notices cannot be muted away: the whole point is that
        # the one party who can act hears it.
        mandatory=True,
    ))


async def tell_owner(platform_db, *, account_id: int, person) -> bool:
    """Tell the account owner that ``person``'s login is under closer
    watch.  Returns whether it went out.  Never raises: the standing is
    already written and the operator already knows.
    """
    try:
        if not await enabled(platform_db):
            return False
        account = await platform_db.get_account(account_id)
        if account is None or (getattr(account, "kind", "real") or "real") != "real":
            # A test account has no customer behind it to tell.
            return False
        owner = await platform_db.get_primary_owner(account_id)
        if owner is None or int(owner.id) == int(person.id):
            # The owner's own login is the one under watch — the login
            # most likely to be in the wrong hands.  Telling it tells
            # them.  The operator reaches the customer another way.
            return False

        who = (getattr(person, "display_name", "") or getattr(person, "email", "")
               or f"user {person.id}")
        _register_category()
        from capabilities.notifications.channels import NotificationContent
        from capabilities.notifications.service import notify_user
        content = NotificationContent(
            category=CATEGORY,
            severity="warning",
            title="A team member's login is under closer watch",
            body=(
                f"Unusual activity was noticed on {who}'s login, and it has "
                f"been placed under closer watch. Nothing is blocked — they "
                f"can keep working as normal.\n\n"
                f"If you do not recognise this activity: change their "
                f"password and end their active sessions from Team "
                f"Management, and ask them about it.\n\n"
                f"If it was them, nothing needs doing."
            ),
        )
        await notify_user(platform_db, account_id, int(owner.id), content)
        return True
    except Exception:
        logger.warning(
            "owner notice: could not tell the owner of account %s about user %s",
            account_id, getattr(person, "id", None), exc_info=True)
        return False
