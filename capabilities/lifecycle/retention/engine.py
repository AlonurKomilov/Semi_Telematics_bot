"""Retention engine — resolves declared needs to a keep-window per target
and runs each target's prune executor.

One pass over platform-scoped targets, one pass per active account over
tenant-scoped targets.  Behavior-preserving: the executors call the
existing storage prune methods, so this changes *organization*, not the
delete logic.  Per-target failures are isolated + logged — one bad
target never aborts the rest.
"""

from __future__ import annotations

import logging

from .registry import ResolvedRetention, resolve

logger = logging.getLogger(__name__)


def _drivers(r: ResolvedRetention) -> str:
    return ", ".join(sorted({n.feature for n in r.needs}))


async def prune_tenant_targets(tenant_db, account_id: int) -> dict[str, int]:
    """Run every tenant-scoped target for one account.

    Returns ``{target_key: rows_deleted}`` — every resolved tenant target
    is present (0 when it deleted nothing or failed) so callers can
    aggregate per-target totals across accounts."""
    out: dict[str, int] = {}
    for r in resolve(scope="tenant"):
        out.setdefault(r.target.key, 0)
        try:
            deleted = await r.target.prune(tenant_db, account_id, r.keep_days)
            out[r.target.key] += deleted or 0
            if deleted:
                logger.info(
                    "retention acct=%d target=%s kept=%dd deleted=%d (needed by: %s)",
                    account_id, r.target.key, r.keep_days, deleted, _drivers(r),
                )
        except Exception:
            logger.exception(
                "retention acct=%d target=%s failed", account_id, r.target.key,
            )
    return out


async def prune_platform_targets(platform_db) -> dict[str, int]:
    """Run every platform-scoped (global) target once.

    Returns ``{target_key: rows_deleted}`` (0 per target when nothing was
    deleted or the prune failed)."""
    out: dict[str, int] = {}
    for r in resolve(scope="platform"):
        out.setdefault(r.target.key, 0)
        try:
            deleted = await r.target.prune(platform_db, None, r.keep_days)
            out[r.target.key] += deleted or 0
            if deleted:
                logger.info(
                    "retention[platform] target=%s kept=%dd deleted=%d (needed by: %s)",
                    r.target.key, r.keep_days, deleted, _drivers(r),
                )
        except Exception:
            logger.exception("retention[platform] target=%s failed", r.target.key)
    return out
