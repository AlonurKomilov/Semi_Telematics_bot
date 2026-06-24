"""Roll-up hub — generic time-series downsampling (live → 5-min → hourly →
daily).

``discover()`` imports every module that registers a cascade so its
``register_cascade`` call fires — the same lazy, idempotent "collect feature
contributions" pattern the Retention hub uses.  This hub holds only the
registry + engine; the aggregation lives in each owning feature's
``rollups.py``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Modules that register a roll-up cascade.  Each owns the stream it
# downsamples + its aggregation.  Add a line when a new high-frequency stream
# needs tiered history; missing modules are skipped (logged), never fatal.
_CONTRIBUTORS = (
    "features.vehicles.rollups",   # vehicle state → 5-min → hourly → daily
)

_discovered = False


def discover() -> None:
    global _discovered
    if _discovered:
        return
    import importlib

    for mod in _CONTRIBUTORS:
        try:
            importlib.import_module(mod)
        except Exception:
            logger.exception("rollups: failed to load contributor module %s", mod)

    _discovered = True
