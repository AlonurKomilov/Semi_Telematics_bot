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
from capabilities.integrations.shared.resolver import (
    resolve_all_providers_for,
)
from infra.services import get_tenant_db, get_telematics_client

logger = logging.getLogger(__name__)


async def _driver_links(tenant, account_id: int, provider_id: str) -> dict:
    """The provider's driver id → our ``users.id``.

    This function used to name Samsara out loud and return nothing for
    anybody else — the one place the feature was not provider-agnostic,
    and it said so:

        the link lives in ``users.samsara_driver_id``, a vendor-named
        column that predates this arc.  A second ELD needs a general
        ``driver_provider_links`` table, which is its own change.

    That table exists now, and the storage reader merges the two legacy
    vendor columns UNDER it, so Samsara and Datatruck keep every link
    they hold while any provider can gain one. The vendor name is gone
    from this file entirely.

    A driver with no link is still written, with ``user_id`` NULL: the
    provider reports their clocks whether or not an admin has matched
    them, and dropping them would make the account's answer quietly
    incomplete.
    """
    try:
        return await tenant.driver_links_for(account_id, provider_id)
    except Exception:
        # A feed that runs unlinked is the state every account starts
        # in; a feed that does not run is not.
        logger.exception(
            "eld: driver link lookup failed acct=%d provider=%s",
            account_id, provider_id,
        )
        return {}


async def _ingest_one(account_id: int, provider_id: str, tenant) -> int:
    """Mirror ONE provider's duty clocks.  Returns rows written.

    Contained per provider on purpose: one ELD being unreachable must
    cost that ELD's drivers and nobody else's.  An account running five
    companies on one device and the rest on another cannot be made to
    go dark by whichever one happens to fail first.
    """
    try:
        provider = await get_telematics_client(account_id, provider_id)
        snapshots = await provider.get_driver_hos()
    except Exception:
        logger.exception(
            "eld: hos fetch failed acct=%d provider=%s",
            account_id, provider_id,
        )
        return 0

    if not snapshots:
        # The provider answered and had nothing to say.  We do NOT
        # clear the table: a driver the ELD stopped reporting keeps
        # their last reading and reads as stale, where a wipe would
        # make them look like a driver who does not exist.
        return 0

    links = await _driver_links(tenant, account_id, provider_id)
    rows = [dataclasses.asdict(s) for s in snapshots]
    written = await tenant.upsert_driver_hos(
        account_id, provider_id, rows, links=links,
    )
    logger.info(
        "eld: hos acct=%d provider=%s drivers=%d linked=%d",
        account_id, provider_id, written,
        sum(1 for r in rows if r.get("provider_driver_id") in links),
    )
    return written


async def ingest_driver_hos(account_id: int) -> int:
    """Refresh the account's duty clocks, from EVERY connected ELD.

    Returns rows written across all of them.  Returns 0 — never raises —
    when no connected provider offers hours of service.  That is the
    common case on an account with no ELD, and it is a fact about the
    account, not a failure of this job.

    Every provider, not one.  The resolver's single-winner answer is
    right for a capability keyed by the THING — two providers writing
    truck 103's live state would overwrite each other. Hours of service
    is keyed ``(provider, driver)``, upserts per provider and never
    deletes, so two ELDs coexist in the store by construction and what
    they produce is a UNION of disjoint driver sets. An account with
    five companies on one device and the rest on another has every
    driver on exactly one certified ELD; asking only the first in
    catalog order does not resolve a conflict, it hides half the fleet.

    That is what happened on the account this was written against: an
    ELD was connected, five keys were green, and the page stayed empty
    because a telematics integration declared earlier in the catalog
    was the only one being asked.
    """
    providers = await resolve_all_providers_for(
        account_id, Capability.DRIVER_HOS,
    )
    if not providers:
        return 0

    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0

    total = 0
    for provider_id in providers:
        total += await _ingest_one(account_id, provider_id, tenant)

    if len(providers) > 1:
        logger.info(
            "eld: hos acct=%d merged %d providers (%s) rows=%d",
            account_id, len(providers), ", ".join(providers), total,
        )
        await _warn_on_shared_drivers(account_id, tenant)
    return total


async def _warn_on_shared_drivers(account_id: int, tenant) -> None:
    """The one case that IS a conflict: one human on two devices.

    Two ELDs normally report disjoint drivers, and the union is simply
    correct.  Mid-migration they can overlap — the same person logged on
    both — and then two certified devices are each authoritative about
    the same driver's hours.

    We do NOT merge that, and deliberately.  Choosing which device is
    right is a compliance judgement that belongs to the operator and
    their ELD vendors, not to a mirror. Both readings stay, each
    labelled with the device it came from, and this says so out loud so
    the ambiguity is discoverable rather than something a dispatcher
    meets as a duplicated name.
    """
    try:
        rows = await tenant.get_driver_hos_live(account_id)
    except Exception:
        logger.exception("eld: shared-driver check failed acct=%d", account_id)
        return
    by_user: dict[int, set[str]] = {}
    for r in rows:
        uid = r.get("user_id")
        if uid is None:
            continue
        by_user.setdefault(int(uid), set()).add(str(r.get("provider_id") or ""))
    shared = {u: p for u, p in by_user.items() if len(p) > 1}
    if shared:
        logger.warning(
            "eld: acct=%d has %d driver(s) reported by MORE THAN ONE ELD "
            "(%s) — both readings kept; choosing between two certified "
            "devices is the operator's call, not ours",
            account_id, len(shared),
            "; ".join(f"user {u}: {','.join(sorted(p))}"
                      for u, p in sorted(shared.items())),
        )
