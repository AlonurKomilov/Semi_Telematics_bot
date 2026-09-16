"""Swap the vendor; nothing above the adapter changes.

This is the test the whole arc exists to make passable.  A provider
that has never been heard of — no Samsara code path, no entry anybody
wrote for it in advance — registers itself, declares it serves hours of
service, and the SAME ingest fills the SAME table through it.

Cheaper than waiting for Motive, and it fails for the right reason:
if any part of the feature learns a vendor's name, this stops working.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import capabilities.integrations.shared.resolver as resolver
import features.eld.ingest as ingest
from adapters.telematics.catalog import ProviderCatalogEntry, ProviderStatus
from adapters.telematics.protocol import (
    Capability, DutyStatus, HosClock, HosSnapshot, TelematicsProvider,
)


class FakeEldProvider:
    """An ELD nobody has written a line of feature code for."""

    provider_id = "fake_eld"
    supported_capabilities = frozenset({Capability.DRIVER_HOS})
    # Every provider states which countdowns it fills.  This one fills
    # all four (see ``_snap``), and saying so is not optional: the
    # protocol requires the declaration, because a surface that cannot
    # tell "reports nothing" from "reported nothing today" will print
    # one as the other.
    hos_clocks_reported = HosClock.ALL

    def __init__(self, snapshots):
        self._snapshots = snapshots
        self.asked = 0

    async def test_connection(self, creds):  # pragma: no cover - unused
        return None

    async def close(self):  # pragma: no cover - unused
        return None

    async def get_vehicles_overview(self):
        return []

    async def get_safety_events(self):
        return []

    async def get_vehicle_health(self):
        return []

    async def get_vehicle_faults(self):
        return []

    async def get_stats_history(self, types, start_iso, end_iso):
        return {}

    async def get_vehicle_spec(self):
        # This fake is an ELD, not a second opinion on the registry.
        return []

    async def get_driver_hos(self):
        self.asked += 1
        return list(self._snapshots)


def _snap(pdid, **kw):
    base = dict(
        provider_driver_id=pdid,
        duty_status=DutyStatus.ON_DUTY,
        drive_remaining_seconds=1800,
        shift_remaining_seconds=5400,
        cycle_remaining_seconds=200000,
        break_in_seconds=30000,
        last_status_change="2026-09-14T07:00:00+00:00",
        source_ts="2026-09-14T09:30:00+00:00",
        driver_name=f"Fake Driver {pdid}",
    )
    base.update(kw)
    return HosSnapshot(**base)


class _Store:
    """Records what the ingest handed the storage layer."""

    def __init__(self, users=()):
        self.calls = []
        self._users = list(users)

    async def upsert_driver_hos(self, account_id, provider_id, rows, *,
                                links=None):
        self.calls.append({
            "account_id": account_id,
            "provider_id": provider_id,
            "rows": rows,
            "links": dict(links or {}),
        })
        return len(rows)

    async def list_account_users(self, account_id):
        return list(self._users)


@pytest.fixture
def wired(monkeypatch):
    """One unknown vendor, connected, offering hours of service."""
    store = _Store()
    provider_holder = {}

    def _install(snapshots, *, provider_id="fake_eld", users=()):
        provider = FakeEldProvider(snapshots)
        provider_holder["p"] = provider
        store._users = list(users)

        catalog = {provider_id: ProviderCatalogEntry(
            provider_id=provider_id,
            display_name="Fake ELD",
            tagline="",
            description="",
            capabilities=frozenset({Capability.DRIVER_HOS}),
            auth_kind="api_token",
            status=ProviderStatus.AVAILABLE,
        )}
        monkeypatch.setattr(resolver, "PROVIDER_CATALOG", catalog)
        monkeypatch.setattr(resolver, "is_registered", lambda pid: True)

        class _PDB:
            async def get_account_integration(self, account_id, pid):
                return SimpleNamespace(status="connected", feature_toggles={})

        import infra.platform as platform
        monkeypatch.setattr(platform, "get_platform_db", lambda: _PDB())

        async def _tenant(account_id):
            return store

        async def _client(account_id, pid, **kw):
            assert pid == provider_id
            return provider

        monkeypatch.setattr(ingest, "get_tenant_db", _tenant)
        monkeypatch.setattr(ingest, "get_telematics_client", _client)
        return store, provider

    return _install


@pytest.mark.asyncio
async def test_an_unknown_vendor_fills_the_same_table(wired):
    store, provider = wired([_snap("fd-1"), _snap("fd-2")])

    written = await ingest.ingest_driver_hos(7)

    assert written == 2
    assert provider.asked == 1
    call = store.calls[0]
    assert call["provider_id"] == "fake_eld"
    assert {r["provider_driver_id"] for r in call["rows"]} == {"fd-1", "fd-2"}


@pytest.mark.asyncio
async def test_the_feature_never_names_a_vendor(wired):
    """The ingest module's CODE, all of it.

    This check used to carve out ``_driver_links``, which read
    ``users.samsara_driver_id`` and returned nothing for anybody else —
    the one place the feature was not agnostic, exempted in the very
    test that was supposed to prove it was. The exemption is gone with
    its reason: ``driver_provider_links`` holds the link for any
    provider and the storage reader merges the legacy vendor columns
    underneath. An exception in a guard outlives the thing it was for
    unless removing it is part of the change that fixes it.

    CODE, not prose: the rule is that the feature must not USE a
    vendor's name — in an identifier, an attribute, a column or a
    literal it passes — and never that it may not explain WHY something
    changed. A history note is how the next reader learns the rule; a
    guard that forbids it just deletes the explanation.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ingest))
    # Drop docstrings — a module's, a function's, a class's — and keep
    # every other string, because a vendor name in a literal that gets
    # PASSED somewhere is exactly the leak this is for.
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body.pop(0)

    used: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.append(node.id)
        elif isinstance(node, ast.Attribute):
            used.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.append(node.value)
    haystack = " ".join(used).lower()

    for vendor in ("samsara", "motive", "geotab", "orient"):
        assert vendor not in haystack, (
            f"{vendor!r} is USED in features/eld/ingest — the feature "
            "learned a provider's name"
        )


@pytest.mark.asyncio
async def test_our_words_survive_the_trip(wired):
    store, _ = wired([_snap("fd-1", duty_status=DutyStatus.PERSONAL_CONVEYANCE)])
    await ingest.ingest_driver_hos(7)

    row = store.calls[0]["rows"][0]
    assert row["duty_status"] == "personal_conveyance"
    assert row["source_ts"] == "2026-09-14T09:30:00+00:00"
    assert row["cycle_remaining_seconds"] == 200000


@pytest.mark.asyncio
async def test_an_unlinkable_vendor_still_records_the_clocks(wired):
    """No link table exists for a second ELD yet.  Every row arrives
    unlinked rather than being dropped — the provider knows these
    drivers even if we cannot yet attach them to our roster."""
    store, _ = wired([_snap("fd-1")])
    await ingest.ingest_driver_hos(7)

    assert store.calls[0]["links"] == {}
    assert len(store.calls[0]["rows"]) == 1


@pytest.mark.asyncio
async def test_no_connected_provider_writes_nothing_and_does_not_raise(
    monkeypatch,
):
    """The common case on an account with no ELD.  A fact about the
    account, not a failure of the job."""
    async def _none(account_id, capability):
        return []

    monkeypatch.setattr(ingest, "resolve_all_providers_for", _none)
    assert await ingest.ingest_driver_hos(7) == 0


@pytest.mark.asyncio
async def test_a_provider_that_raises_is_contained(wired, monkeypatch):
    store, provider = wired([_snap("fd-1")])

    async def _boom():
        raise RuntimeError("upstream 503")

    provider.get_driver_hos = _boom
    assert await ingest.ingest_driver_hos(7) == 0
    assert store.calls == []


@pytest.mark.asyncio
async def test_an_empty_answer_does_not_clear_the_table(wired):
    """A driver the ELD stopped reporting keeps their last reading and
    reads as stale.  Wiping would make them look like a driver who
    does not exist."""
    store, _ = wired([])
    assert await ingest.ingest_driver_hos(7) == 0
    assert store.calls == [], "an empty answer must not reach the store"


def test_the_fake_satisfies_the_same_protocol_as_the_real_ones():
    assert isinstance(FakeEldProvider([]), TelematicsProvider)
