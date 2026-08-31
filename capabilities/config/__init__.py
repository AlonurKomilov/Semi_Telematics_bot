"""The config family — one home for both scopes.

    ROLE     page_layouts rows       can_manage_config_role   own role
    ACCOUNT  account_settings rows   can_manage_config_all    account-wide

WHAT WAS WRONG BEFORE.  The family had no code home at all.  Its account
half was ``capabilities/settings_registry.py`` — the only bare module
among sixteen capability packages — and its role half had no capability
presence whatsoever, living entirely as an interface router plus an
adapter.  The rules were written down (capabilities/config/docs/ARCHITECTURE.md) and
enforced by tests, but a developer asking "where does config live?" got a
document and a scavenger hunt.

WHAT DOES NOT LIVE HERE, deliberately.  A feature's OWN config endpoints
stay with that feature (``/<feature>/config``, defined in the feature's
own module).  This package is the machinery both scopes share — the
ownership registry, the role wall, the flag names — not a central place
to put configuration.  Collecting the endpoints here would rebuild the
one-router-for-everyone that the config arc dismantled: a single
``PUT /settings`` that accepted any key with one permission, and so let
one feature's Manage write two sibling features' configuration.

Same shape as ``capabilities/data_lifecycle``, which groups rollups
(BUILD) and retention (DELETE) under one family with shared ``_common``
while keeping the engines separate — because the halves answer different
questions on different cadences, exactly as these two do.

SSOT for the rules: capabilities/config/docs/ARCHITECTURE.md.
"""

from capabilities.config._common import (  # noqa: F401
    ACCOUNT_FLAG, ROLE_FLAG, SCOPE_FLAG, Kind, Scope,
)
from capabilities.config.account import (  # noqa: F401
    SELF_ONLY, SETTING_OWNERS, SYSTEM_ONLY, SettingOwner, owner_for,
)
from capabilities.config.role import (  # noqa: F401
    ALLOWED_FEATURES, VALID_ROLES, may_manage_config_role,
)

__all__ = [
    # shared vocabulary
    "Scope", "Kind", "ACCOUNT_FLAG", "ROLE_FLAG", "SCOPE_FLAG",
    # account scope
    "SettingOwner", "SETTING_OWNERS", "owner_for", "SYSTEM_ONLY", "SELF_ONLY",
    # role scope
    "ALLOWED_FEATURES", "VALID_ROLES", "may_manage_config_role",
]
