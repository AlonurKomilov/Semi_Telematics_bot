"""The sentence nothing in the repo could say: which provider serves X?

The vendor-neutral contract was already here — protocol, capability
vocabulary, provider registry, catalog with lifecycle states — but
every consumer still named "samsara" out loud, so the seam carried no
weight.  These pin the rules of the resolver that makes it load-bearing,
and especially the one place its defaults must NOT copy the fan-out
helper's.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import capabilities.integrations.shared.resolver as res
from adapters.telematics.catalog import ProviderCatalogEntry, ProviderStatus


CAP = "test_capability"


def _entry(provider_id, *, caps=(CAP,), status=ProviderStatus.AVAILABLE):
    return ProviderCatalogEntry(
        provider_id=provider_id,
        display_name=provider_id.title(),
        tagline="",
        description="",
        capabilities=frozenset(caps),
        auth_kind="api_token",
        status=status,
    )


def _row(status="connected", toggles=None):
    return SimpleNamespace(status=status, feature_toggles=toggles or {})


@pytest.fixture
def world(monkeypatch):
    """A catalog, a registry and a platform DB we fully control.

    Catalog order matters to the tie-break, so the fixture builds an
    ordered dict rather than relying on whatever the real catalog
    happens to declare today.
    """
    state = {"catalog": {}, "registered": set(), "rows": {}}

    monkeypatch.setattr(res, "PROVIDER_CATALOG", state["catalog"])
    monkeypatch.setattr(
        res, "is_registered", lambda pid: pid in state["registered"])

    class _DB:
        async def get_account_integration(self, account_id, provider_id):
            return state["rows"].get(provider_id)

    import infra.platform as platform
    monkeypatch.setattr(platform, "get_platform_db", lambda: _DB())
    return state


def _offer(state, provider_id, *, registered=True, **kw):
    state["catalog"][provider_id] = _entry(provider_id, **kw)
    if registered:
        state["registered"].add(provider_id)


@pytest.mark.asyncio
async def test_a_connected_provider_is_resolved(world):
    _offer(world, "alpha")
    world["rows"]["alpha"] = _row()

    got = await res.resolve_provider_for(1, CAP)
    assert got is not None
    assert got.provider_id == "alpha"
    assert got.capability == CAP


@pytest.mark.asyncio
async def test_no_integration_row_resolves_to_nothing(world):
    """The one rule this must NOT copy from the fan-out helper.

    ``_for_each_account_with_capability`` RUNS a job when an account
    has no integration row — a rollout-era default that kept existing
    pipelines alive.  Here the question is "which provider", and an
    account with no ELD connected must get None.  Guessing a provider
    that was never connected is how a compliance surface ends up
    answering from nothing.
    """
    _offer(world, "alpha")
    # no row at all
    assert await res.resolve_provider_for(1, CAP) is None


@pytest.mark.asyncio
async def test_a_disconnected_provider_does_not_qualify(world):
    _offer(world, "alpha")
    world["rows"]["alpha"] = _row(status="paused")
    assert await res.resolve_provider_for(1, CAP) is None


@pytest.mark.asyncio
async def test_an_explicit_off_switch_is_honoured(world):
    _offer(world, "alpha")
    world["rows"]["alpha"] = _row(toggles={CAP: {"enabled": False}})
    assert await res.resolve_provider_for(1, CAP) is None


@pytest.mark.asyncio
async def test_a_toggle_map_that_predates_the_capability_means_on(world):
    """Silence is not OFF.  A row written before this capability
    existed has no opinion about it, and reading that as off would
    leave a newly-shipped feed dark on every existing account until
    somebody clicked something."""
    _offer(world, "alpha")
    world["rows"]["alpha"] = _row(toggles={"something_else": {"enabled": True}})

    got = await res.resolve_provider_for(1, CAP)
    assert got is not None and got.provider_id == "alpha"


@pytest.mark.asyncio
async def test_a_provider_that_does_not_offer_it_is_not_asked(world):
    _offer(world, "alpha", caps=("other_capability",))
    world["rows"]["alpha"] = _row()
    assert await res.resolve_provider_for(1, CAP) is None


@pytest.mark.asyncio
async def test_a_coming_soon_entry_is_never_resolved(world):
    """Catalog previews have no client.  Resolving one would hand the
    caller a KeyError from the registry instead of an honest None."""
    _offer(world, "alpha", status=ProviderStatus.COMING_SOON,
           registered=False)
    world["rows"]["alpha"] = _row()
    assert await res.resolve_provider_for(1, CAP) is None


@pytest.mark.asyncio
async def test_a_catalog_entry_with_no_registered_client_is_skipped(world):
    """The drift case: the catalog says AVAILABLE but nothing
    registered the class."""
    _offer(world, "alpha", registered=False)
    world["rows"]["alpha"] = _row()
    assert await res.resolve_provider_for(1, CAP) is None


@pytest.mark.asyncio
async def test_two_connected_providers_pick_the_first_in_catalog_order(
    world, caplog,
):
    """A fleet mid-migration really can have two ELDs connected.
    Merging two sets of duty clocks is a decision nobody has asked for,
    so we pick one deterministically and say which."""
    _offer(world, "alpha")
    _offer(world, "beta")
    world["rows"]["alpha"] = _row()
    world["rows"]["beta"] = _row()

    with caplog.at_level("WARNING"):
        got = await res.resolve_provider_for(1, CAP)

    assert got is not None and got.provider_id == "alpha"
    assert "alpha, beta" in caplog.text


@pytest.mark.asyncio
async def test_the_tie_break_follows_the_catalog_not_the_alphabet(world):
    """Declaration order decides, so the winner never depends on a
    vendor's name."""
    _offer(world, "zeta")
    _offer(world, "alpha")
    world["rows"]["zeta"] = _row()
    world["rows"]["alpha"] = _row()

    got = await res.resolve_provider_for(1, CAP)
    assert got is not None and got.provider_id == "zeta"


@pytest.mark.asyncio
async def test_a_lookup_that_raises_skips_that_provider(world, monkeypatch):
    """One unreachable integration row must not deny a capability that
    another connected provider can serve."""
    _offer(world, "alpha")
    _offer(world, "beta")
    world["rows"]["beta"] = _row()

    class _DB:
        async def get_account_integration(self, account_id, provider_id):
            if provider_id == "alpha":
                raise RuntimeError("platform db unreachable")
            return world["rows"].get(provider_id)

    import infra.platform as platform
    monkeypatch.setattr(platform, "get_platform_db", lambda: _DB())

    got = await res.resolve_provider_for(1, CAP)
    assert got is not None and got.provider_id == "beta"


def test_providers_offering_is_empty_for_an_unknown_capability(world):
    _offer(world, "alpha")
    assert res.providers_offering("nobody_offers_this") == []


@pytest.mark.asyncio
async def test_no_client_is_built_when_nothing_resolves(world, monkeypatch):
    """Building a client opens sessions and prefetches org ids.  A
    caller asking whether anything serves a capability should not pay
    for that."""
    built = []

    async def _build(account_id, provider_id, **kw):
        built.append(provider_id)
        return object()

    import infra.services as services
    monkeypatch.setattr(services, "get_telematics_client", _build)

    assert await res.resolve_client_for(1, CAP) is None
    assert built == []
