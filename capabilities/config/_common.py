"""Vocabulary both config scopes share.

The config family has exactly TWO scopes and they are not variations on
one thing — they answer different questions, live in different stores,
and are granted by different flags:

    ROLE     "how does MY TEAM see this page?"     page_layouts rows
             can_manage_config_role                own role only

    ACCOUNT  "what does this feature DO for
              everyone?"                            account_settings rows
             can_manage_config_all                  account-wide

This module exists so the flag names are written ONCE.  They were spelled
out as string literals in a router, a registry, two frontend files and
four tests; a typo in any of them fails open or fails closed silently,
because a permission that does not exist is simply absent from a
FeatureSet and ``getattr(perms, flag, False)`` returns False without
complaint.

SSOT for the rules themselves: docs/architecture/config.md.
"""

from __future__ import annotations

from typing import Literal

# The two scopes, and the flag that grants each.
Scope = Literal["account", "role"]

ACCOUNT_FLAG = "can_manage_config_all"
ROLE_FLAG = "can_manage_config_role"

SCOPE_FLAG: dict[str, str] = {
    "account": ACCOUNT_FLAG,
    "role": ROLE_FLAG,
}

# What a stored key IS, which is not the same question as who may write
# it.  ``config`` is the answer for every account_settings row; the other
# three exist to make the exceptions VISIBLE rather than silent:
#
#   system      platform infrastructure with no account-level action at
#               all (AI model pinning — AI is a service, and services
#               have nothing to grant).
#   preference  per-user state that config.md puts in the preferences
#               service, declared here only because it is in the wrong
#               store and should be findable.
#   manage      retained so an older declaration still parses; nothing
#               uses it, and test_settings_registry forbids new ones.
Kind = Literal["manage", "config", "system", "preference"]
