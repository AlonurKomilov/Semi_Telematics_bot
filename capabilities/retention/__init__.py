"""DEPRECATED import path — retention moved to ``capabilities.lifecycle.retention``.

Kept as a thin re-export ONLY because an in-flight branch
(``features/drivers/retention.py``) still imports the old path and we don't
want to sweep its uncommitted changes.  Remove this shim once that branch
lands and its import is updated to ``capabilities.lifecycle.retention``.
"""

from __future__ import annotations

from capabilities.lifecycle.retention import discover  # noqa: F401
