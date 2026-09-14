"""Which provider serves capability X for account A?

Until now nothing in the codebase answered that question.  The
vendor-neutral contract was all there — a protocol, a capability
vocabulary, a provider registry, a catalog with lifecycle states — but
every consumer still named ``"samsara"`` out loud:
``_for_each_account_with_capability`` defaults ``provider_id`` to it,
and the Samsara ingest imports the Samsara-shaped client directly.  The
seam existed; it carried no weight.

This module is the missing sentence.  A feature declares the capability
it needs (``features/<x>/lifecycle.py``, already the convention) and
asks here which provider — if any — can serve it for one account.
That is what lets a second ELD slot in behind the same feature without
the feature learning its name.

Why this does NOT reuse the fan-out helper's rules
--------------------------------------------------
``_for_each_account_with_capability`` runs a job when an account has no
integration row at all, on purpose: it predates the rollout that gave
every account a row, and stopping those pipelines would have been worse
than running them.  That default is wrong here, and dangerously so.
This function's question is "which provider", and "none is connected"
is a real, common, correct answer — an account with no ELD connected
must get ``None``, not a guess.  Defaulting to a provider that was
never connected is how a compliance surface ends up answering from
nothing.

One default IS shared, because it is the same decision: a toggle map
written before this capability existed does not mean the capability is
off.  A missing toggle reads as enabled; only an explicit ``False``
disables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from adapters.telematics.catalog import PROVIDER_CATALOG, ProviderStatus
from adapters.telematics.registry import is_registered

logger = logging.getLogger(__name__)


# Catalog statuses whose providers may actually be asked for data.
# COMING_SOON entries are metadata-only previews with no client; a
# DEPRECATED one still works for accounts already on it, which is the
# whole point of that state, so it stays resolvable.
_USABLE = frozenset({
    ProviderStatus.AVAILABLE,
    ProviderStatus.BETA,
    ProviderStatus.DEPRECATED,
})


@dataclass(frozen=True)
class ResolvedProvider:
    """The provider that will serve one capability for one account."""

    provider_id: str
    capability: str


def providers_offering(capability: str) -> list[str]:
    """Catalog ids that claim ``capability`` and have a client, in
    catalog order.

    Catalog order is the tie-break when an account has connected more
    than one qualifying provider, so it must be deterministic — it is
    the literal declaration order in ``PROVIDER_CATALOG``, which is a
    plain dict.  Sorting alphabetically instead would make the winner
    depend on a vendor's name.
    """
    return [
        provider_id
        for provider_id, entry in PROVIDER_CATALOG.items()
        if capability in entry.capabilities
        and entry.status in _USABLE
        and is_registered(provider_id)
    ]


def _toggle_allows(integration, capability: str) -> bool:
    """Whether the account's toggle map permits this capability.

    Missing means enabled: a row written before the capability existed
    has no opinion about it, and reading silence as OFF would leave a
    newly-shipped feed dark on every existing account until somebody
    clicked something.  Only an explicit ``False`` disables.
    """
    cfg = (integration.feature_toggles or {}).get(capability) or {}
    return cfg.get("enabled", True) is not False


async def resolve_provider_for(
    account_id: int,
    capability: str,
) -> ResolvedProvider | None:
    """The provider serving ``capability`` for ``account_id``, or None.

    ``None`` is a first-class answer meaning "no connected provider
    offers this", and callers must treat it as such — an ELD feed with
    no ELD behind it has to say so, never answer from an empty table.

    A connected provider qualifies when the catalog says it offers the
    capability, its client is registered, its integration row says
    ``connected``, and the account has not switched the capability off.
    When several qualify the first in catalog order wins and the rest
    are logged: two ELDs on one account is a real situation (a fleet
    mid-migration) but merging two sets of duty clocks is a decision
    nobody has asked for yet, so we pick one and say which.
    """
    from infra.platform import get_platform_db

    candidates = providers_offering(capability)
    if not candidates:
        return None

    db = get_platform_db()
    qualified: list[str] = []
    for provider_id in candidates:
        try:
            integration = await db.get_account_integration(
                account_id, provider_id,
            )
        except Exception:
            logger.exception(
                "resolver: integration lookup failed acct=%d provider=%s",
                account_id, provider_id,
            )
            continue
        if integration is None or integration.status != "connected":
            continue
        if not _toggle_allows(integration, capability):
            continue
        qualified.append(provider_id)

    if not qualified:
        return None
    if len(qualified) > 1:
        logger.warning(
            "resolver: acct=%d has %d providers offering %s (%s) — "
            "using %s by catalog order",
            account_id, len(qualified), capability,
            ", ".join(qualified), qualified[0],
        )
    return ResolvedProvider(provider_id=qualified[0], capability=capability)


async def resolve_client_for(account_id: int, capability: str):
    """The protocol-shaped client that serves ``capability``, or None.

    Deliberately separate from :func:`resolve_provider_for`: building a
    client opens sessions and prefetches org ids, and a caller that
    only wants to know *whether* anything serves a capability should
    not pay for that.  Resolving first and constructing second also
    keeps the resolver testable without touching HTTP at all.
    """
    resolved = await resolve_provider_for(account_id, capability)
    if resolved is None:
        return None
    from infra.services import get_telematics_client

    return await get_telematics_client(account_id, resolved.provider_id)
