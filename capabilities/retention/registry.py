"""Cross-cutting data-retention registry — the Retention hub.

Retention is not a feature and not telemetry-specific: every feature
accumulates data (DB rows, and later object-store files) that must be
kept for a while, then deleted.  This hub centralizes the *policy* while
keeping ownership feature-centric, exactly like the Alerting/Reporting
hubs:

  * Features declare *how long they need a target kept* via
    ``register_need`` (``features/<x>/retention.py``).
  * The platform declares *how to prune each target* via
    ``register_target`` (``capabilities/retention/targets.py``).
  * ``resolve()`` keeps each target for the MAX of its declared needs —
    so a shared table (e.g. ``vehicle_state_snapshot``, read by several
    features) is retained as long as the hungriest consumer requires,
    and the magic numbers become a contract you can read.

The engine then runs each target's prune executor.  One hub, every
feature contributes; ``capabilities/retention`` knows nothing about
telemetry or scorecards internals — only targets and needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

# (db, account_id, keep_days) -> rows deleted.  ``account_id`` is None for
# platform-scoped targets; ``db`` is the tenant DB (tenant scope) or the
# platform DB (platform scope).
PruneExecutor = Callable[[Any, "int | None", int], Awaitable[int]]


@dataclass(frozen=True)
class RetentionTarget:
    """How to prune one data target (declared by the platform layer)."""

    key: str            # stable id, e.g. "vehicle_state_snapshot"
    label: str          # human label
    scope: str          # "tenant" (per-account) | "platform" (global)
    prune: PruneExecutor


@dataclass(frozen=True)
class RetentionNeed:
    """A feature's stated requirement to keep a target for ``keep_days``."""

    feature: str
    target: str         # RetentionTarget.key
    keep_days: int
    reason: str


@dataclass(frozen=True)
class ResolvedRetention:
    target: RetentionTarget
    keep_days: int                 # max across declared needs
    needs: tuple[RetentionNeed, ...]


_TARGETS: dict[str, RetentionTarget] = {}
_NEEDS: list[RetentionNeed] = []


def register_target(target: RetentionTarget) -> None:
    _TARGETS[target.key] = target


def register_need(need: RetentionNeed) -> None:
    _NEEDS.append(need)


def resolve(scope: str | None = None) -> list[ResolvedRetention]:
    """Each registered target that at least one feature claims, paired with
    the MAX keep-window across its needs.  A target with NO declared need
    is skipped — we never prune data nobody has claimed (safety)."""
    out: list[ResolvedRetention] = []
    for key, target in _TARGETS.items():
        if scope is not None and target.scope != scope:
            continue
        needs = tuple(n for n in _NEEDS if n.target == key)
        if not needs:
            continue
        keep = max(n.keep_days for n in needs)
        out.append(ResolvedRetention(target, keep, needs))
    return out
