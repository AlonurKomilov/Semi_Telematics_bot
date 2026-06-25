"""DEPRECATED import path — the Samsara connect-time backfill moved to
``capabilities.integrations.samsara.backfill`` (it orchestrates the Samsara
ingest, so it belongs with the integration, not the warehouse).

Kept as a thin re-export ONLY for an in-flight branch (``capabilities/jobs/
functions.py``) that still imports the old path and whose uncommitted WIP we
don't want to sweep.  Remove this shim once that branch updates its import to
``capabilities.integrations.samsara.backfill``.
"""

from __future__ import annotations

from capabilities.integrations.samsara.backfill import (  # noqa: F401
    backfill_account_initial,
    schedule_backfill,
)
