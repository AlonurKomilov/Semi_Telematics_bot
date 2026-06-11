"""Smoke tests for the M2 client resolver + Samsara provider wrapper.

Verifies that:

  * The legacy ``adapters.samsara.*`` import paths still resolve to
    the same classes as the new ``adapters.telematics.samsara.*``
    paths — the back-compat shim is transparent.
  * Importing the umbrella ``adapters.telematics`` package
    auto-registers ``SamsaraProvider`` in the registry without any
    explicit call.
  * ``SamsaraProvider`` satisfies the runtime-checkable
    ``TelematicsProvider`` protocol.
  * The provider's ``supported_capabilities`` agrees with the
    catalog (the import-time drift guard already enforces this; the
    test makes the contract explicit).
  * Provider methods proxy to the underlying ``MultiCompanyClient``
    correctly when invoked with a stub client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.telematics import (
    PROVIDER_CATALOG,
    TelematicsProvider,
    get_provider,
    is_registered,
)
from adapters.telematics.samsara import SamsaraProvider


# ── Shim parity ───────────────────────────────────────────────────


def test_legacy_import_paths_resolve_to_same_classes():
    """``from adapters.samsara.client import MultiCompanyClient``
    must return the same class object as the new path so isinstance
    checks across the codebase don't fragment."""
    from adapters.samsara.client import MultiCompanyClient as Legacy
    from adapters.telematics.samsara.client import MultiCompanyClient as New
    assert Legacy is New

    from adapters.samsara.circuit_breaker import samsara_breaker as legacy_breaker
    from adapters.telematics.samsara.circuit_breaker import samsara_breaker as new_breaker
    assert legacy_breaker is new_breaker


def test_legacy_package_re_exports_all_public_surface():
    """``from adapters.samsara import build_multi_company_client``
    must still resolve — many call sites use the package-level form."""
    from adapters.samsara import (  # noqa: F401
        MultiCompanyClient,
        SamsaraClient,
        SamsaraUnavailable,
        build_multi_company_client,
        populate_company_display,
        samsara_breaker,
    )


# ── Registration ──────────────────────────────────────────────────


def test_samsara_auto_registers_on_umbrella_import():
    """Importing ``adapters.telematics`` should be enough — no
    explicit register_provider call required from caller code."""
    assert is_registered("samsara")
    assert get_provider("samsara") is SamsaraProvider


def test_samsara_provider_satisfies_runtime_protocol():
    """``runtime_checkable`` protocol so isinstance() works at runtime
    for any caller that wants to type-narrow."""
    stub_client = MagicMock()
    instance = SamsaraProvider(stub_client)
    assert isinstance(instance, TelematicsProvider)


def test_catalog_status_matches_registration_state():
    """Drift guard: AVAILABLE / BETA catalog entries must have a
    registered provider class; COMING_SOON / DEPRECATED entries must
    NOT.  A mismatch means an owner could click Connect on a
    provider whose implementation doesn't exist, or be told a
    working provider is "coming soon"."""
    from adapters.telematics import PROVIDER_CATALOG, ProviderStatus, is_registered

    for provider_id, entry in PROVIDER_CATALOG.items():
        if entry.status in (ProviderStatus.AVAILABLE, ProviderStatus.BETA):
            assert is_registered(provider_id), (
                f"catalog says {provider_id!r} is {entry.status.value} but "
                "no implementation is registered — the dashboard would offer "
                "Connect on a provider that would 503 at first call"
            )
        elif entry.status == ProviderStatus.COMING_SOON:
            assert not is_registered(provider_id), (
                f"catalog says {provider_id!r} is COMING_SOON but an "
                "implementation IS registered — flip the catalog status to "
                "AVAILABLE so the dashboard surfaces the Connect button"
            )
        # DEPRECATED is intentionally permissive — implementations can
        # linger to serve existing connected accounts while new
        # connects are blocked at the UI layer.


def test_catalog_capabilities_are_well_known_strings():
    """Every capability id in the catalog must match one of the
    well-known ``Capability.*`` constants.  Free-form strings would
    silently disable themselves in the scheduler fan-out (which
    matches by exact string)."""
    from adapters.telematics import PROVIDER_CATALOG
    from adapters.telematics.protocol import Capability

    known = {
        v for k, v in vars(Capability).items()
        if not k.startswith("_") and isinstance(v, str)
    }
    for provider_id, entry in PROVIDER_CATALOG.items():
        for cap in entry.capabilities:
            assert cap in known, (
                f"catalog entry {provider_id!r} declares unknown capability "
                f"{cap!r} — add it to ``protocol.Capability`` or fix the "
                "catalog typo"
            )


# ── Capability contract ───────────────────────────────────────────


def test_provider_capabilities_match_catalog():
    """The import-time drift guard would have failed already, but a
    test makes the contract explicit and locatable from the test
    output if someone breaks it later."""
    catalog_caps = PROVIDER_CATALOG["samsara"].capabilities
    provider_caps = SamsaraProvider.supported_capabilities
    assert catalog_caps == provider_caps


def test_provider_provider_id_is_samsara():
    assert SamsaraProvider.provider_id == "samsara"


# ── Proxy behaviour ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_fleet_overview_proxies_to_underlying_client():
    stub = MagicMock()
    stub.get_fleet_overview = AsyncMock(return_value=[{"id": "v1", "name": "103"}])
    provider = SamsaraProvider(stub)
    vehicles = await provider.get_fleet_overview()
    stub.get_fleet_overview.assert_awaited_once()
    assert vehicles == [{"id": "v1", "name": "103"}]


@pytest.mark.asyncio
async def test_get_safety_events_forwards_days_kwarg():
    stub = MagicMock()
    stub.get_events = AsyncMock(return_value=[])
    provider = SamsaraProvider(stub)
    await provider.get_safety_events(days=7)
    stub.get_events.assert_awaited_once_with(days=7)


@pytest.mark.asyncio
async def test_get_vehicle_faults_strips_total_and_breakdown_tuple_elements():
    """``MultiCompanyClient.get_vehicles_with_faults`` returns
    ``(rows, total_active, per_company_breakdown)``; the protocol
    shape returns just the rows."""
    stub = MagicMock()
    stub.get_vehicles_with_faults = AsyncMock(return_value=(
        [{"id": "v1"}, {"id": "v2"}],
        2,
        {"ACME": {"total": 10, "faulted": 2, "dtcs": 4}},
    ))
    provider = SamsaraProvider(stub)
    rows = await provider.get_vehicle_faults()
    assert rows == [{"id": "v1"}, {"id": "v2"}]


@pytest.mark.asyncio
async def test_test_connection_ok_reports_each_company_reached():
    """test_connection now probes /me per company instead of fanning
    out to the rate-limited stats endpoints.  Verify the per-company
    probe is fast + reports the company count + grabs the org id.

    The probe (``_probe_one_company``) opens a fresh aiohttp session
    against ``client.base_url`` — stubbed at the method level since
    this test cares about the aggregation, not the HTTP call."""
    stub = MagicMock()
    stub.clients = {"ACME": MagicMock(), "BETA": MagicMock()}
    provider = SamsaraProvider(stub)

    async def fake_probe(code, c):  # (code, org_id, error) — the real shape
        return code, {"ACME": "samsara_org_42", "BETA": "samsara_org_43"}[code], None
    provider._probe_one_company = fake_probe

    status = await provider.test_connection({})
    assert status.ok is True
    assert "2 companies" in status.message
    assert status.provider_account_id == "samsara_org_42"


@pytest.mark.asyncio
async def test_test_connection_failure_returns_error_message():
    stub = MagicMock()
    stub.clients = {"ACME": MagicMock()}
    provider = SamsaraProvider(stub)

    async def fake_probe(code, c):
        return code, None, f"{code}: credentials rejected (HTTP 401 Unauthorized)"
    provider._probe_one_company = fake_probe

    status = await provider.test_connection({})
    assert status.ok is False
    assert "401 Unauthorized" in status.message


@pytest.mark.asyncio
async def test_stats_history_merges_across_per_company_clients():
    """Samsara historical fetches are per-org; the provider merges
    them into one dict keyed by vehicle_id."""
    company_a = MagicMock()
    company_a._get_paginated_history = AsyncMock(return_value={
        "vid-A": {"name": "alpha", "obdOdometerMeters": [{"time": "t1", "value": 1}]},
    })
    company_b = MagicMock()
    company_b._get_paginated_history = AsyncMock(return_value={
        "vid-B": {"name": "bravo", "obdOdometerMeters": [{"time": "t1", "value": 2}]},
    })

    multi = MagicMock()
    multi.clients = {"AAA": company_a, "BBB": company_b}
    provider = SamsaraProvider(multi)

    merged = await provider.get_stats_history(
        ["obdOdometerMeters"],
        "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
    )
    assert set(merged.keys()) == {"vid-A", "vid-B"}
    assert merged["vid-A"]["_org"] == "AAA"
    assert merged["vid-B"]["_org"] == "BBB"


@pytest.mark.asyncio
async def test_stats_history_skips_failing_company_without_failing_others():
    """One company's HTTP failure shouldn't abort the whole backfill."""
    company_ok = MagicMock()
    company_ok._get_paginated_history = AsyncMock(return_value={
        "vid-A": {"name": "alpha"},
    })
    company_broken = MagicMock()
    company_broken._get_paginated_history = AsyncMock(side_effect=RuntimeError("boom"))

    multi = MagicMock()
    multi.clients = {"OK": company_ok, "BROKEN": company_broken}
    provider = SamsaraProvider(multi)

    merged = await provider.get_stats_history(
        ["obdOdometerMeters"],
        "2026-01-01T00:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
    )
    # The healthy company's data still shows up.
    assert "vid-A" in merged
    assert merged["vid-A"]["_org"] == "OK"


# ── Resolver wiring ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_telematics_client_raises_for_unknown_provider(monkeypatch):
    """Unknown / unregistered provider ids surface as KeyError from
    the registry — the resolver should not silently fall back."""
    from infra import services

    with pytest.raises(KeyError):
        await services.get_telematics_client(99999, provider_id="not-a-provider")


@pytest.mark.asyncio
async def test_get_telematics_client_caches_per_account_and_provider(monkeypatch):
    """Successive calls for the same (account, provider) return the
    same wrapper instance — caches keyed correctly."""
    from infra import services

    # Stub get_client so we don't touch the real Samsara construction.
    fake_client = MagicMock()
    monkeypatch.setattr(
        services, "get_client", AsyncMock(return_value=fake_client),
    )
    services._provider_cache.clear()

    a1 = await services.get_telematics_client(42, "samsara")
    a2 = await services.get_telematics_client(42, "samsara")
    assert a1 is a2
    assert isinstance(a1, SamsaraProvider)
