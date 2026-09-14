"""ACQUIRE: mirror one account's duty clocks from whichever ELD it has.

The whole point of the arc is visible in one function here — nothing
below names a vendor.  ``resolve_provider_for`` answers which connected
provider serves hours of service for this account; the protocol turns
that provider's reply into our vocabulary; the store keys on what the
provider gave us.  A second ELD costs a protocol method and a mapper in
its own adapter, and not one line of this file.
"""

from __future__ import annotations

import dataclasses
import logging

from adapters.telematics.protocol import Capability
from capabilities.integrations.shared.resolver import resolve_provider_for
from infra.services import get_tenant_db, get_telematics_client

logger = logging.getLogger(__name__)


async def _driver_links(tenant, account_id: int, provider_id: str) -> dict:
    """The provider's driver id → our ``users.id``.

    This is the one part of the feature that is NOT yet
    provider-agnostic, and saying so is better than hiding it: the link
    lives in ``users.samsara_driver_id``, a vendor-named column that
    predates this arc.  A second ELD needs a general
    ``driver_provider_links`` table, which is its own change.

    Until then an unmatched provider simply returns no links, and the
    store writes the rows anyway with ``user_id`` NULL — the driver's
    clocks are still recorded and still shown, just not yet attached to
    a person on our roster.  Dropping them instead would make the
    account's HOS answer quietly incomplete.
    """
    if provider_id != "samsara":
        return {}
    try:
        users = await tenant.list_account_users(account_id)
    except Exception:
        logger.exception("eld: user lookup failed acct=%d", account_id)
        return {}
    return {
        sid: u.id
        for u in users
        if (sid := str(getattr(u, "samsara_driver_id", "") or "").strip())
    }


async def ingest_driver_hos(account_id: int) -> int:
    """Refresh the account's duty clocks.  Returns rows written.

    Returns 0 — never raises — when no connected provider offers hours
    of service.  That is the common case on an account with no ELD, and
    it is a fact about the account, not a failure of this job.
    """
    resolved = await resolve_provider_for(account_id, Capability.DRIVER_HOS)
    if resolved is None:
        return 0

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0

    try:
        provider = await get_telematics_client(account_id, resolved.provider_id)
        snapshots = await provider.get_driver_hos()
    except Exception:
        logger.exception(
            "eld: hos fetch failed acct=%d provider=%s",
            account_id, resolved.provider_id,
        )
        return 0

    if not snapshots:
        # The provider answered and had nothing to say.  We do NOT
        # clear the table: a driver the ELD stopped reporting keeps
        # their last reading and reads as stale, where a wipe would
        # make them look like a driver who does not exist.
        return 0

    links = await _driver_links(tenant, account_id, resolved.provider_id)
    rows = [dataclasses.asdict(s) for s in snapshots]
    written = await tenant.upsert_driver_hos(
        account_id, resolved.provider_id, rows, links=links,
    )
    logger.info(
        "eld: hos acct=%d provider=%s drivers=%d linked=%d",
        account_id, resolved.provider_id, written,
        sum(1 for r in rows if r.get("provider_driver_id") in links),
    )
    return written
