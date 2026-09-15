"""A SECOND OPINION on trucks somebody else already registered.

Two integrations can describe the same truck. Samsara knows its VIN,
ORIENT ELD knows its plate, and each can be missing what the other has.
``capabilities/source`` exists for exactly that — several sources, one
record, arbitrated field by field with ``manual`` above all of them and
the account owner choosing the order in Vehicles → Config.

Why it is worth a feed of its own: the feature reads the MERGED
vehicle, never "Samsara's vehicle". When one integration goes dark —
an outage, a revoked token, a contract that ended — the VIN and plate
keep arriving from the other and nothing downstream notices. That is
the whole point of a source layer, and it is not a hypothetical: it is
why an owner asks for a second provider in the first place.

FILL-ONLY, AND STRUCTURALLY SO
------------------------------
This feed may never CREATE a vehicle. Not "is configured not to" —
may not, by the argument it passes.

The registry is what billing counts (``BILLABLE_VEHICLE_TYPES``, the
daily ``billing_quantity_sync``). ``recon.may_add`` fails OPEN —
"unknown sources may" — which is correct for a source that has always
registered vehicles and wrong for one being added to a live account
with trucks already on the invoice. A brand-new source that creates
even a handful of rows moves a customer's bill, silently, on its first
tick.

So ``may_create=False`` travels with every call from here. A source
that gives a second opinion about a truck is not a source that decides
the truck exists.
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
    """Merge one provider's vehicle spec onto rows that already exist."""
    try:
        provider = await get_telematics_client(account_id, provider_id)
        rows = await provider.get_vehicle_spec()
    except Exception:
        logger.exception(
            "vehicle spec: fetch failed acct=%d provider=%s",
            account_id, provider_id,
        )
        return 0
    if not rows:
        return 0

    try:
        return await tenant.project_external_vehicles(
            account_id, rows,
            vehicle_type="truck",
            source=provider_id,
            # The whole contract of this module, in one argument.
            may_create=False,
        )
    except Exception:
        logger.exception(
            "vehicle spec: merge failed acct=%d provider=%s",
            account_id, provider_id,
        )
        return 0


async def ingest_vehicle_spec(account_id: int) -> int:
    """Fill vehicle spec gaps from every provider that offers one.

    Returns rows merged.  Returns 0 — never raises — when nothing
    offers it, which is the common case and a fact about the account.

    Every provider, not one: this is a union of opinions, and the merge
    below is what resolves them.  Asking only the first in catalog order
    would throw away the second opinion that is the entire reason the
    feed exists.
    """
    providers = await resolve_all_providers_for(
        account_id, Capability.VEHICLE_SPEC,
    )
    if not providers:
        return 0

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0

    total = 0
    for provider_id in providers:
        total += await _fill_from_one(account_id, provider_id, tenant)
    if total:
        logger.info(
            "vehicle spec: acct=%d merged %d row(s) from %s",
            account_id, total, ", ".join(providers),
        )
    return total
