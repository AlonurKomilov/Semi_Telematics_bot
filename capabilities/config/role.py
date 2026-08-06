"""Config, ROLE scope — a role's default arrangement of a feature page.

The other half of the family (see ``account.py`` for the account-wide
half).  A role manager saves their team's default layout for a feature
page; every teammate's page starts from it, and each user's personal
preference still applies on top — a default, not a lock.

WHY THIS IS ONE CENTRAL MODULE AND NOT ONE PER FEATURE.  Account config
is per-feature: each feature owns its own ``/<feature>/config`` endpoint
and its own keys.  Role config is not shaped that way — there is ONE
endpoint (``PUT /page-layouts/{role}/{feature}``) that takes the feature
as a path parameter and validates it against the allow-list below.  A
``features/<x>/config/role.py`` per feature would be six empty files and
one wrapper around this.  When a feature grows genuinely per-feature role
config, that is the moment to split it out — not before.

The HTTP handlers stay in ``interfaces/api/page_layouts.py``: only the
interface layer may import ``interfaces.api.deps``.  What lives here is
the part that is not routing — who may write, and what is a valid target.
"""

from __future__ import annotations

from capabilities.config._common import ROLE_FLAG
from capabilities.permissions.roles import Role, get_user_permissions

__all__ = [
    "ALLOWED_FEATURES", "VALID_ROLES", "ROLE_FLAG", "may_manage_config_role",
]

# Feature pages that HAVE configurable sections.  Grows one entry per
# page that opts into the gear — an allow-list, so the table can't
# accumulate rows for keys no page will ever read.
ALLOWED_FEATURES = frozenset({"alerts"})

# Roles a team default may exist for — the dashboard personas that render
# Pattern-B pages.  Driver is deliberately absent (the Mini App is their
# surface); owner/admin ARE present so an owner can tune their own view's
# default too.
VALID_ROLES = frozenset({
    "owner", "admin", "fleet", "dispatcher", "safety",
    "hr", "accounting", "recruiter",
})


async def may_manage_config_role(user: dict, role: str) -> bool:
    """The matrix decides WHO; code decides HOW FAR.

    Resolves the caller's EFFECTIVE permission set (their own tier row,
    per-account overrides included), so whatever the owner ticked in the
    Permissions matrix is the authority:
      * ``can_manage_account``      → any role's default,
      * ``can_manage_config_role``  → the caller's OWN role's only.
    The own-role wall stays hard-coded — delegating the grant to a fleet
    employee can never extend to the safety team's page.
    """
    own = user.get("role", "")
    try:
        perms = await get_user_permissions(
            Role(own),
            user["account_id"],
            is_manager=bool(user.get("is_manager")),
            is_primary_owner=bool(user.get("is_primary_owner")),
        )
    except ValueError:
        return False        # a role string Role() doesn't know
    if perms.can_manage_account:
        return True
    return bool(getattr(perms, ROLE_FLAG, False)) and own == role
