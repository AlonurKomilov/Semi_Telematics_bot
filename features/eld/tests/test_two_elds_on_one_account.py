"""Two connected integrations both offer hours of service. Which wins?

This is not hypothetical and it is not rare: an account can run one
vendor for telematics and a different one as its actual electronic
logging device.  Both declare ``Capability.DRIVER_HOS``, both are
connected, and exactly one of them is the device the drivers' logs
actually live on.

The resolver's rule is "first in CATALOG order wins, the rest are
logged" — declaration order, so the winner never depends on a vendor's
name.  Against the real catalog that means **Samsara wins over ORIENT
ELD**, because Samsara is declared first.  On an account whose real ELD
is ORIENT, that is the wrong answer, and the failure is quiet: the job
succeeds, the page fills with Samsara's rows, and nobody is told that a
second ELD was skipped.

The escape is the per-capability toggle the resolver already honours:
turning OFF the telematics provider's "Hours of service" switch hands
the capability to the ELD.  That is an operator action, not a code
path, which is exactly why it is pinned here — the day this ordering
changes, somebody's duty clocks change source without a deploy note.

These use the REAL catalog on purpose.  A fixture catalog would pass
forever while the shipped one said something else.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import capabilities.integrations.shared.resolver as resolver
from adapters.telematics.catalog import PROVIDER_CATALOG
from adapters.telematics.protocol import Capability


def _connected(**toggles_by_provider):
    """A platform DB where the named providers are connected.

    Value ``True``/``False`` sets the driver_hos toggle explicitly;
    ``None`` leaves the toggle map empty, which the resolver reads as
    enabled — the rollout default that keeps existing feeds alive.
    """
    class _DB:
        async def get_account_integration(self, account_id, provider_id):
            if provider_id not in toggles_by_provider:
                return None
            enabled = toggles_by_provider[provider_id]
            toggles = ({} if enabled is None
                       else {Capability.DRIVER_HOS: {"enabled": enabled}})
            return SimpleNamespace(status="connected", feature_toggles=toggles)
    return _DB()


@pytest.fixture
def platform(monkeypatch):
    def _install(db):
        import infra.platform as platform_mod
        monkeypatch.setattr(platform_mod, "get_platform_db", lambda: db)
    return _install


# ── The catalog fact the rule depends on ──────────────────────────

def test_both_are_declared_hos_providers_in_the_shipped_catalog():
    offering = [
        pid for pid, e in PROVIDER_CATALOG.items()
        if Capability.DRIVER_HOS in e.capabilities
    ]
    assert "samsara" in offering
    assert "orient_eld" in offering, (
        "ORIENT ELD must declare hours of service — it is an ELD"
    )


def test_samsara_is_declared_before_orient_and_therefore_wins_ties():
    """Pinned because the tie-break is declaration order.  Reordering
    the catalog silently changes whose duty clocks an account sees."""
    order = list(PROVIDER_CATALOG)
    assert order.index("samsara") < order.index("orient_eld")


# ── What that means for an account running both ───────────────────

@pytest.mark.asyncio
async def test_only_the_eld_connected_resolves_to_the_eld(platform):
    platform(_connected(orient_eld=None))
    got = await resolver.resolve_provider_for(1, Capability.DRIVER_HOS)
    assert got is not None and got.provider_id == "orient_eld"


@pytest.mark.asyncio
async def test_both_connected_hands_hours_to_the_telematics_provider(
    platform,
):
    """The quiet one.  Connecting an ELD while a telematics provider is
    already serving hours of service changes NOTHING by itself."""
    platform(_connected(samsara=None, orient_eld=None))
    got = await resolver.resolve_provider_for(1, Capability.DRIVER_HOS)
    assert got is not None and got.provider_id == "samsara", (
        "catalog order decides, and Samsara is declared first"
    )


@pytest.mark.asyncio
async def test_turning_off_the_other_providers_toggle_hands_it_over(
    platform,
):
    """The operator action that makes a real ELD the source of truth.
    An explicit False is the only thing that disqualifies a connected
    provider — a missing toggle reads as enabled."""
    platform(_connected(samsara=False, orient_eld=None))
    got = await resolver.resolve_provider_for(1, Capability.DRIVER_HOS)
    assert got is not None and got.provider_id == "orient_eld"


@pytest.mark.asyncio
async def test_turning_off_the_eld_instead_gives_it_back(platform):
    platform(_connected(samsara=None, orient_eld=False))
    got = await resolver.resolve_provider_for(1, Capability.DRIVER_HOS)
    assert got is not None and got.provider_id == "samsara"


@pytest.mark.asyncio
async def test_both_switched_off_is_no_provider_not_a_fallback(platform):
    """"No connected provider offers this" is a first-class answer.
    Falling back to one the operator switched off would answer a
    compliance question from a source they deliberately refused."""
    platform(_connected(samsara=False, orient_eld=False))
    assert await resolver.resolve_provider_for(1, Capability.DRIVER_HOS) is None


@pytest.mark.asyncio
async def test_the_contention_is_logged_by_name(platform, caplog):
    """The only signal this happened.  If it ever stops being logged,
    an account's duty clocks can change source with nothing to read."""
    import logging
    platform(_connected(samsara=None, orient_eld=None))
    with caplog.at_level(logging.WARNING):
        await resolver.resolve_provider_for(1, Capability.DRIVER_HOS)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "samsara" in joined and "orient_eld" in joined
