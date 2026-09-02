"""Unit width — the second Team Management scope question, shared core.

Permissions answer "may this role do this VERB on this FEATURE"; Team
Management answers "which UNITS".  This module is the ONE
implementation of the width answer during the verb/scope bridge, worn
by two thin adapters: ``interfaces.api.deps.member_unit_scope`` for
API surfaces (a JWT user dict) and the bot's handlers (a DB ``User``
row they already hold).  One implementation, because the width rule
duplicated per surface is the rule that drifts — the company wall's
``company_allows`` lesson, applied again.

Two claims, EITHER of which narrows (both live during the bridge):

  * the effective GRANT is vehicle-only — the legacy pair the matrix
    still edits, honoured per-account via ``can_for_account``;
  * the member's three-layer Team Management scope (member override ⊃
    account role width ⊃ built-in role default).

When the pairs die in the cleanup stage the first claim goes with
them, and this collapses into the model predicate plus the role layer.
"""

from __future__ import annotations

import logging
from typing import Optional

from capabilities.permissions.roles import (
    PAIRED_UNIT_FEATURES,
    Role,
    can_for_account,
)

_log = logging.getLogger(__name__)


async def role_scope_layer(
    account_id: int, role, platform_db=None,
) -> Optional[str]:
    """The account's ROLE-level width, or None for "built-in default".

    Its own try/except on purpose: a missing role layer must never
    change anyone's width — before it existed, every account resolved
    member-override-then-built-in, and None reproduces exactly that.
    """
    try:
        if platform_db is None:
            from infra.platform import get_platform_db
            platform_db = get_platform_db()
        return await platform_db.get_role_vehicle_scope(
            int(account_id),
            role.value if hasattr(role, "value") else str(role),
        )
    except Exception:
        _log.debug("role vehicle scope unavailable", exc_info=True)
        return None


async def unit_width(
    account_id: int, role, db_user, feature: str,
) -> str:
    """'all' or 'assigned' for one paired unit feature.

    ``db_user`` is the member's DB row when the caller has it (the bot
    always does), or None when it could not be loaded — which fails
    OPEN to 'all' for a wide-granted caller, deliberately: asked
    "should this WIDE-granted request be narrowed", an unloadable row
    means we cannot know of an override, and inventing one would make
    a wide-granted caller's data vanish whenever the platform hiccups.
    The cautious fail-closed answer belongs to the OTHER question
    ("what is this member's scope" — deps.get_member_vehicle_scope).
    """
    wide_flag, _narrow_flag = PAIRED_UNIT_FEATURES[feature]
    role_enum = role if isinstance(role, Role) else Role(role)
    if not await can_for_account(int(account_id), role_enum, wide_flag):
        return "assigned"
    if db_user is None:
        return "all"
    return db_user.scope_with_role_default(
        await role_scope_layer(account_id, role_enum))
