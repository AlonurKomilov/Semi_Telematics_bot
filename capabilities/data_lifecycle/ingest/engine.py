"""Ingest engine — run a declared dataset and leave a record.

The record is the point.  ``ingest_runs`` holds one row per
(dataset, account, run): rows written and the freshest ``source_ts``
seen — which is exactly what every post-mortem in this repair arc had
to reconstruct by hand from logs, row counts and guesswork.  With it,
"this dataset wrote nothing for three days" and "this data is older
than its SLA" become one generic query instead of a forensic project.

Fan-out mirrors the rollup engine's, with the integration gate the
hand-wired jobs carried: a dataset only runs for accounts whose
provider is connected and whose feature toggle allows it.
"""

from __future__ import annotations

import logging

from .registry import IngestDataset

logger = logging.getLogger(__name__)


async def run_dataset(dataset: IngestDataset) -> None:
    """Run one dataset across every eligible account, recording each run."""
    from capabilities.integrations.shared.helpers import (
        _for_each_account_with_capability,
    )

    async def _run_and_record(account_id: int) -> int:
        from infra.platform import get_tenant_db

        rows = await dataset.run(account_id)
        try:
            tenant = await get_tenant_db(account_id)
            if tenant is not None:
                await tenant.record_ingest_run(
                    account_id, dataset.key, int(rows or 0),
                )
        except Exception:
            # The recording must never take the ingest down with it —
            # but a recording failure is itself worth a loud line,
            # because a blind watchdog is how the last five datasets
            # died.
            logger.exception(
                "ingest_runs recording failed dataset=%s acct=%d",
                dataset.key, account_id,
            )
        return rows

    await _for_each_account_with_capability(dataset.capability, _run_and_record)
