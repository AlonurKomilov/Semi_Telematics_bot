"""DEPRECATED — re-export of ``capabilities.lifecycle.retention.registry``.

Thin shim for the one in-flight importer (``features/drivers/retention.py``);
remove once that branch updates to the new path.
"""

from __future__ import annotations

from capabilities.lifecycle.retention.registry import (  # noqa: F401
    PruneExecutor,
    ResolvedRetention,
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
    resolve,
)
