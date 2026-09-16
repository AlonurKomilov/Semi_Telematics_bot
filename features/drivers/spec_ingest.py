"""A SECOND OPINION on people somebody already hired.

The argument is the one ``features/vehicles/spec_ingest`` makes about
trucks, and it is the account owner's argument rather than mine: a
feature reads the MERGED driver, not "Datatruck's driver". When one
integration goes dark — an outage, a revoked token, a contract that
ended — the phone number and the licence keep arriving from the other
and nothing downstream notices.

WHAT IS DIFFERENT ABOUT PEOPLE
------------------------------
A truck is matched by its unit number, printed on its door. A person is
matched ONLY by a link an admin made by hand in Drivers → Integrations.

That is not caution for its own sake. It was measured on the live
account: our roster had 8 drivers, 0 licence numbers and 5 emails;
ORIENT reported 26 drivers, 26 licence numbers and 12 emails, and the
email overlap between the two was ZERO. Every automatic key available
would have linked nobody — and the one key that looked promising, the
licence number, is the field this feed WRITES. A matcher that fell back
to it would, on its second tick, agree with the licence it wrote on the
first. A wrong link would become its own evidence.

FILL-ONLY, AND STRUCTURALLY SO
------------------------------
``project_provider_driver_spec`` has no INSERT in it. Not "is
configured not to create people" — cannot, because the statement is
missing from the code.

The providers reaching here describe a person without ever saying
whether they still work for the carrier: ORIENT's roster carries no
role, no status, no active flag and no termination date. A source that
cannot tell a current driver from somebody who left in March is not a
source that gets to decide who is on the roster. It gets to fill in a
phone number for somebody who is already on it.

WHAT IT LOGS
------------
Counts, never values. Which driver got which licence number is
answered per field by ``driver_field_provenance``, on the row itself,
where it belongs — not by a log line that would copy driver PII into
every log sink the platform ships to.
"""

from __future__ import annotations

import logging

from adapters.telematics.protocol import Capability
from capabilities.integrations.shared.resolver import (
    resolve_all_providers_for,
)
from infra.services import get_tenant_db, get_telematics_client

logger = logging.getLogger(__name__)


async def _fill_from_one(
    account_id: int, provider_id: str, tenant,
) -> int:
    """Merge one provider's driver identity onto members already here."""
    try:
        provider = await get_telematics_client(account_id, provider_id)
        rows = await provider.get_driver_spec()
    except Exception:
        logger.exception(
            "driver spec: fetch failed acct=%d provider=%s",
            account_id, provider_id,
        )
        return 0
    if not rows:
        return 0

    try:
        counts = await tenant.project_provider_driver_spec(
            account_id, provider_id, rows,
        )
    except Exception:
        logger.exception(
            "driver spec: merge failed acct=%d provider=%s",
            account_id, provider_id,
        )
        return 0

    if counts["written"] or counts["skipped_unlinked"]:
        # The unlinked count is the actionable half: it is the number an
        # admin can change, by linking those drivers.
        logger.info(
            "driver spec: acct=%d provider=%s linked=%d written=%d "
            "cdl_filled=%d conflicts=%d unlinked=%d",
            account_id, provider_id, counts["linked"], counts["written"],
            counts["filled_cdl"], counts["conflicts"],
            counts["skipped_unlinked"],
        )
    return int(counts["written"])


async def ingest_driver_spec(account_id: int) -> int:
    """Fill driver identity gaps from every provider that offers one.

    Returns rows merged. Returns 0 — never raises — when nothing offers
    it, which is the common case and a fact about the account rather
    than a failure.

    Every provider, not one: this is a union of opinions and the merge
    resolves them. Asking only the first in catalog order would throw
    away the second opinion that is the entire reason the feed exists.
    """
    providers = await resolve_all_providers_for(
        account_id, Capability.DRIVER_SPEC,
    )
    if not providers:
        return 0

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0

    total = 0
    for provider_id in providers:
        total += await _fill_from_one(account_id, provider_id, tenant)
    return total
