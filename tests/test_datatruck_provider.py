"""DatatruckProvider + DatatruckClient — unit tests.

Locks in:
  * Catalog drift guard: the catalog and provider agree on capabilities
    (the provider module's import-time assertion).
  * Subdomain normalisation: pasting "premier", "premier.datatruck.io",
    or "https://premier.datatruck.io/" all reduce to the same slug.
  * test_connection() error mapping: 401/403 → "credentials rejected",
    404 → "wrong subdomain", success → carries the visible-driver count.
  * No accidental telematics drift: the provider's
    supported_capabilities is exactly the five TMS_* values and does
    NOT include VEHICLE_STATE / SAFETY_EVENTS etc.

Does not touch the real Datatruck network — every probe stubs the
client's ``_get`` to return a chosen (status, body) tuple.
"""

from __future__ import annotations

import pytest

from adapters.telematics.catalog import PROVIDER_CATALOG, ProviderStatus
from adapters.telematics.datatruck.client import DatatruckClient
from adapters.telematics.datatruck.provider import DatatruckProvider
from adapters.telematics.protocol import Capability


# ── Catalog ───────────────────────────────────────────────────


def test_datatruck_catalog_is_available_today():
    entry = PROVIDER_CATALOG["datatruck"]
    assert entry.status == ProviderStatus.AVAILABLE
    assert entry.auth_kind == "api_token_with_subdomain"


def test_datatruck_catalog_and_provider_agree_on_capabilities():
    """The catalog drift guard inside ``provider.py`` already raises
    at import time if the sets diverge — this test makes the
    contract explicit so a reviewer reading the test file sees what
    the guard enforces."""
    cat_caps = PROVIDER_CATALOG["datatruck"].capabilities
    prov_caps = DatatruckProvider.supported_capabilities
    assert cat_caps == prov_caps
    assert cat_caps == frozenset({
        Capability.TMS_DRIVERS_SYNC,
        Capability.TMS_TRUCKS_SYNC,
        Capability.TMS_TRAILERS_SYNC,
        Capability.TMS_ORDERS_SYNC,
        Capability.TMS_WORK_ORDERS_SYNC,
    })


def test_datatruck_does_not_claim_telematics_capabilities():
    """Datatruck is a TMS, not a telematics platform — calling its
    fleet/safety/health/faults methods is allowed but returns empty.
    The protocol-level capability set must NOT advertise telematics
    capabilities or the scheduler would queue jobs that always
    return empty results."""
    prov_caps = DatatruckProvider.supported_capabilities
    for forbidden in (
        Capability.VEHICLE_STATE,
        Capability.SAFETY_EVENTS,
        Capability.VEHICLE_HEALTH,
        Capability.VEHICLE_FAULTS,
        Capability.HISTORY_BACKFILL,
        Capability.STATE_SNAPSHOT_HISTORY,
    ):
        assert forbidden not in prov_caps


# ── Subdomain normalisation ──────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("premier", "premier"),
    ("PREMIER", "premier"),
    ("  premier  ", "premier"),
    ("premier.datatruck.io", "premier"),
    ("https://premier.datatruck.io", "premier"),
    ("https://premier.datatruck.io/", "premier"),
    ("http://premier.datatruck.io", "premier"),
])
def test_client_normalises_subdomain(raw: str, expected: str):
    c = DatatruckClient(company_subdomain=raw, api_token="t")
    assert c.subdomain == expected
    assert c.base_url == f"https://{expected}.datatruck.io/api/v1/openapi/"


@pytest.mark.parametrize("bad", ["", "  ", "premier.example.com", "pre/mier"])
def test_client_rejects_invalid_subdomain(bad: str):
    with pytest.raises(ValueError):
        DatatruckClient(company_subdomain=bad, api_token="t")


@pytest.mark.parametrize("login_host", [
    "app", "app.datatruck.io", "https://app.datatruck.io",
    "https://app.datatruck.io/", "APP",
])
def test_client_gives_specific_error_for_app_datatruck_io(login_host: str):
    """``app.datatruck.io`` is the consolidated SPA login URL — the most
    likely paste mistake from operators copying their browser URL bar.
    Generic 'not a valid slug' wouldn't explain the actual problem,
    so the client raises a specific, instructive error."""
    with pytest.raises(ValueError, match="shared Datatruck login page"):
        DatatruckClient(company_subdomain=login_host, api_token="t")


def test_client_rejects_missing_token():
    with pytest.raises(ValueError):
        DatatruckClient(company_subdomain="premier", api_token="")


# ── test_connection error mapping ────────────────────────────


@pytest.mark.asyncio
async def test_provider_test_connection_success_reports_driver_count():
    client = DatatruckClient(company_subdomain="premier", api_token="t")

    async def fake_get(path, params=None):
        return 200, {"count": 60, "results": []}

    client._get = fake_get  # type: ignore[method-assign]
    provider = DatatruckProvider(client)

    status = await provider.test_connection({})
    assert status.ok is True
    assert "60 drivers" in status.message
    assert status.provider_account_id == "premier"


@pytest.mark.asyncio
async def test_provider_test_connection_401_means_token_rejected():
    client = DatatruckClient(company_subdomain="premier", api_token="bad")

    async def fake_get(path, params=None):
        return 401, "<unauthorized>"

    client._get = fake_get  # type: ignore[method-assign]
    provider = DatatruckProvider(client)

    status = await provider.test_connection({})
    assert status.ok is False
    assert "credentials rejected" in status.message
    assert "HTTP 401" in status.message


@pytest.mark.asyncio
async def test_provider_test_connection_404_hints_at_wrong_subdomain():
    client = DatatruckClient(company_subdomain="wrong", api_token="t")

    async def fake_get(path, params=None):
        return 404, "<not found>"

    client._get = fake_get  # type: ignore[method-assign]
    provider = DatatruckProvider(client)

    status = await provider.test_connection({})
    assert status.ok is False
    assert "subdomain" in status.message.lower()
