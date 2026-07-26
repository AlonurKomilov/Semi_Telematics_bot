"""Preferences — per-user UI state, as a capability.

Backend half of the preferences domain.  The storage mixin stays in
``adapters/storage/user_preferences.py`` (all DB mixins live there, and
the ``user_preferences`` table's DDL is in ``adapters/storage/schema.py``);
this package owns the HTTP surface and the key rules.

Frontend half: ``interfaces/dashboard/src/preferences/``.
"""

from . import keys, router

__all__ = ["keys", "router"]
