"""Key + value rules for the per-user UI preference store.

Keys are OPAQUE to the backend on purpose — the dashboard coins them
(``table.maintenance-tasks.visibility``, ``notif.position``) and owns
their meaning in its own registry.  The backend only enforces that a key
is safe to put in a URL, a log line and a primary key, and that a single
value can't be used as unbounded storage.

Kept in its own module (not inline in the router) so the limits are
quotable from tests and from the frontend's contract docs.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

# Safe charset: alphanumerics plus the three separators the dashboard's
# key families use (``.`` namespacing, ``_``/``-`` inside ids).
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
# Same charset, but no length floor — an empty prefix means "everything".
PREFIX_RE = re.compile(r"^[A-Za-z0-9._-]{0,200}$")
# 64 KB per single preference.  Generous for UI state (the biggest real
# value is a DataGrid's saved-tab list) while still bounding abuse.
VALUE_MAX = 64 * 1024


def validate_key(key: str) -> None:
    """Raise 422 unless ``key`` is a safe, bounded preference key."""
    if not KEY_RE.match(key or ""):
        raise HTTPException(
            status_code=422,
            detail="Invalid preference key (alphanumeric/./_/- only, max 200 chars)",
        )


def validate_prefix(prefix: str | None) -> None:
    """Raise 422 unless ``prefix`` is a safe key prefix.  ``None`` is fine
    (no narrowing); so is ``""``."""
    if prefix is not None and prefix and not PREFIX_RE.match(prefix):
        raise HTTPException(status_code=422, detail="Invalid prefix")
