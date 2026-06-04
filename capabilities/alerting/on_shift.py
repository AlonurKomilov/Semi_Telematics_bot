"""Per-persona "who is currently on shift?" resolver.

When the per-persona-groups routing mode is active AND an alert is
``CRITICAL``, the resolver attaches @-mentions to the group post so
the people on duty get a phone-pop notification (not just a group
ping that everyone has muted).  This module returns the set of users
who should be mentioned.

Reads from:
  • the existing ``capabilities.alerting.dnd.is_user_on_shift`` helper
    (single source of truth for the on-shift window)
  • the persona → role(s) mapping below

Returns ``OnShiftUser`` records the caller (pipeline.py) uses to build
the mention.  Telegram supports two mention shapes; the caller picks:

  • <a href="tg://user?id=12345">Name</a>  — works without a username,
    pings the user if they're a member of the group
  • @username — needs the user to have a public username set

We expose telegram_id + display_name; the caller decides which shape
to use (in practice the tg:// link wins because most fleet users don't
have a public Telegram username).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from adapters.storage.models import Role, User
from infra.platform import get_platform_db
from infra.services import get_tenant_db

from . import persona_mapping


@dataclass(frozen=True)
class OnShiftUser:
    user_id: int
    telegram_id: int
    display_name: str


# Persona → roles whose members staff that persona.  Mirrors the Role
# enum and persona_mapping.PERSONAS; kept here (not in persona_mapping)
# because the alert-type routing table and the user-role mapping evolve
# independently.
_PERSONA_TO_ROLES: dict[str, tuple[Role, ...]] = {
    persona_mapping.OWNER_ADMIN: (Role.OWNER, Role.ADMIN),
    persona_mapping.DISPATCHER:  (Role.DISPATCHER,),
    persona_mapping.SAFETY:      (Role.SAFETY,),
    persona_mapping.FLEET:       (Role.FLEET,),
    persona_mapping.HR:          (Role.HR,),
}


def roles_for_persona(persona: str) -> tuple[Role, ...]:
    """The roles that staff ``persona`` — empty tuple if unknown."""
    return _PERSONA_TO_ROLES.get(persona, ())


async def users_on_shift_for_persona(
    account_id: int, persona: str,
) -> list[OnShiftUser]:
    """Return active alert-subscribed users with the persona's roles
    who are currently on shift in the account's timezone.

    Empty list when:
      • persona is unknown
      • no users in the account have a matching role
      • all matching users are off-shift right now (DND-window logic
        delegated to ``dnd.is_user_on_shift``)

    Used by the routing resolver to compose @-mentions for CRITICAL
    alerts in per-persona-groups mode.  Non-critical alerts skip this
    path — the group post itself is enough.
    """
    role_set = {r.value for r in roles_for_persona(persona)}
    if not role_set:
        return []

    platform = get_platform_db()
    # Per-account scope: get_all_alert_subscribers returns ALL accounts'
    # subscribers; filter to this account + the persona's role set.
    all_subs: list[User] = await platform.get_all_alert_subscribers()
    candidates = [
        u for u in all_subs
        if u.account_id == account_id and _role_value(u) in role_set
    ]
    if not candidates:
        return []

    # Defer the on-shift check to dnd.is_user_on_shift — which handles
    # the account-tz lookup, work_hours rows, midnight-wrap, and the
    # "no schedule = always on" default.  Local import keeps the
    # module dep-graph one-directional (routing_resolver → on_shift
    # → dnd, no cycle).
    from .dnd import is_user_on_shift

    tenant = await get_tenant_db(account_id)
    on_shift: list[OnShiftUser] = []
    for user in candidates:
        try:
            if await is_user_on_shift(user, tenant):
                on_shift.append(OnShiftUser(
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    display_name=user.display_name or "",
                ))
        except Exception:
            # Any failure inside the shift check treats the user as
            # off-shift — better to under-mention than to crash the
            # whole alert dispatch on a single user's bad work_hours
            # row.  ``dnd.is_user_on_shift`` already has its own
            # defensive log so we stay silent here.
            continue
    return on_shift


def _role_value(user: User) -> str:
    """Normalise ``user.role`` to its string value across enum / str."""
    role = user.role
    return role.value if hasattr(role, "value") else str(role)


def format_mentions(
    users: Iterable[OnShiftUser], *, max_mentions: int = 5,
) -> str:
    """Render an HTML-mention string for Telegram.

    Capped at ``max_mentions`` — past five names the message gets
    unreadable and the group notification storm has already done its
    job.  Returns ``""`` when the iterable is empty so the caller can
    splice it in unconditionally.
    """
    pool = list(users)[:max_mentions]
    if not pool:
        return ""
    parts: list[str] = []
    for u in pool:
        name = u.display_name or "On-shift"
        # tg:// mention works without the user having a public
        # username, as long as they're a member of the group.
        parts.append(
            f'<a href="tg://user?id={u.telegram_id}">{_escape_html(name)}</a>'
        )
    return " ".join(parts)


def _escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )
