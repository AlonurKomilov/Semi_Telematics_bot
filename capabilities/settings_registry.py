"""DEPRECATED shim — moved to ``capabilities.config.account``.

This module was the account half of the config family living as the only
bare ``.py`` among sixteen capability packages, with the role half having
no capability home at all.  Both now sit in ``capabilities/config/``.

Kept as a re-export for one release for the same reason the config URL
aliases are kept: a branch written before the move should not fail to
import mid-merge.  It re-exports the SAME objects — there is no second
registry — so a caller on the old path and one on the new path resolve
identical rules.

Import from ``capabilities.config`` (or ``capabilities.config.account``)
in new code.  When no import of this path remains, delete the file.
"""

from capabilities.config.account import (  # noqa: F401
    SELF_ONLY, SETTING_OWNERS, SYSTEM_ONLY, Kind, SettingOwner, owner_for,
)

__all__ = [
    "SettingOwner", "SETTING_OWNERS", "owner_for",
    "SYSTEM_ONLY", "SELF_ONLY", "Kind",
]
