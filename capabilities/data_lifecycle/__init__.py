"""Data-lifecycle family — the two hubs that move data through its life.

A data tier is born (ingest), promoted to coarser tiers, kept for a window,
then deleted.  This package groups the two engines that run that lifecycle:

  * ``rollups``   — BUILD: downsample a high-frequency stream into tiered
                    history (live → minute → hour → day → week), on each
                    tier's own cadence.
  * ``retention`` — DELETE: prune every registered tier to the window its
                    owning feature declares, in one nightly pass.

They are deliberately **separate engines** (different cadences, multi-consumer
resolution, tenant-vs-platform scope) but **one family**: each feature owns its
contributions in its own module, both fan out via the shared
``_common.for_each_active_account``, and both discover contributors via the
shared ``_common.make_discover``.  This ``__init__`` only documents the family
and offers a combined ``discover()``.
"""

from __future__ import annotations


def discover() -> None:
    """Run both hubs' discovery (idempotent)."""
    from .rollups import discover as discover_rollups
    from .retention import discover as discover_retention

    discover_rollups()
    discover_retention()
