"""Smoke tests for the M3 capability-aware fan-out.

Verifies the toggle-respecting behaviour of
``_for_each_account_with_capability`` in the telemetry ingestor:

  * Accounts whose integration row enables the capability run the
    coroutine.
  * Accounts whose integration row disables the capability skip the
    coroutine.
  * Accounts whose integration row has status != 'connected' skip
    regardless of toggle state.
  * Accounts with no integration row at all DEFAULT TO RUNNING (the
    rollout-safety promise — pre-migration accounts continue to work).
  * Capabilities the toggle map doesn't mention default to enabled
    (a capability the catalog adds AFTER the row was first written
    should not silently disable itself).
  * The cadence resolver returns the catalog default when no
    override is set, the override when present, and an empty dict
    for unknown providers.

Tests run in-process with monkey-patched DB calls so they don't
require Postgres or APScheduler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from adapters.storage.account_integrations import AccountIntegration
from adapters.telematics import (
    PROVIDER_CATALOG,
    Capability,
    resolve_capability_cadence,
)


# ── Cadence resolver ──────────────────────────────────────────────


def test_cadence_resolver_falls_back_to_catalog_default():
    """No override → catalog default."""
    out = resolve_capability_cadence(
        "samsara", Capability.VEHICLE_STATE, cadence_overrides={},
    )
    expected = PROVIDER_CATALOG["samsara"].feature_defaults[
        Capability.VEHICLE_STATE
    ]
    assert out == expected


def test_cadence_resolver_returns_override_when_set():
    """Override present → override (silently capped to default at
    the scheduler tick rate, but we surface the configured value
    so the dashboard can warn)."""
    override = {"interval_sec": 30}
    out = resolve_capability_cadence(
        "samsara", Capability.VEHICLE_STATE,
        cadence_overrides={Capability.VEHICLE_STATE: override},
    )
    assert out == override


def test_cadence_resolver_returns_empty_for_unknown_provider():
    out = resolve_capability_cadence(
        "nonexistent-provider", Capability.VEHICLE_STATE,
    )
    assert out == {}


def test_cadence_resolver_returns_empty_for_unknown_capability():
    out = resolve_capability_cadence("samsara", "unknown_capability")
    assert out == {}


# ── Fan-out toggle semantics ─────────────────────────────────────


class _StubAccount:
    """Minimal duck-type matching what ``list_accounts(active_only=True)``
    returns — only ``.id`` is touched in the fan-out helper."""

    def __init__(self, account_id: int):
        self.id = account_id


def _fake_integration(
    *,
    account_id: int = 1,
    status: str = "connected",
    toggles: dict | None = None,
) -> AccountIntegration:
    return AccountIntegration(
        account_id=account_id,
        provider_id="samsara",
        status=status,
        connected_at="2026-01-01T00:00:00+00:00",
        credentials={},
        feature_toggles=toggles or {},
        cadence_overrides={},
        last_health_at="",
        last_health_error="",
        last_backfill_at="",
        created_by=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_fan_out_runs_when_toggle_enabled(monkeypatch):
    """Account with status=connected + toggle enabled → coro called."""
    from capabilities.telemetry import ingestor

    ran_for: list[int] = []

    async def _job(account_id: int) -> int:
        ran_for.append(account_id)
        return 0

    integration = _fake_integration(
        account_id=42,
        toggles={Capability.VEHICLE_STATE: {"enabled": True}},
    )

    _patch_fan_out_dependencies(
        monkeypatch,
        accounts=[_StubAccount(42)],
        integration_by_acct={42: integration},
    )

    await ingestor._for_each_account_with_capability(
        Capability.VEHICLE_STATE, _job,
    )
    assert ran_for == [42]


@pytest.mark.asyncio
async def test_fan_out_skips_when_toggle_disabled(monkeypatch):
    from capabilities.telemetry import ingestor

    ran_for: list[int] = []

    async def _job(account_id: int) -> int:
        ran_for.append(account_id)
        return 0

    integration = _fake_integration(
        account_id=42,
        toggles={Capability.VEHICLE_STATE: {"enabled": False}},
    )

    _patch_fan_out_dependencies(
        monkeypatch,
        accounts=[_StubAccount(42)],
        integration_by_acct={42: integration},
    )

    await ingestor._for_each_account_with_capability(
        Capability.VEHICLE_STATE, _job,
    )
    assert ran_for == []


@pytest.mark.asyncio
async def test_fan_out_skips_when_status_not_connected(monkeypatch):
    """Status='error' or 'disabled' → skip even if toggle says enabled."""
    from capabilities.telemetry import ingestor

    ran_for: list[int] = []

    async def _job(account_id: int) -> int:
        ran_for.append(account_id)
        return 0

    integration = _fake_integration(
        account_id=42,
        status="error",
        toggles={Capability.VEHICLE_STATE: {"enabled": True}},
    )

    _patch_fan_out_dependencies(
        monkeypatch,
        accounts=[_StubAccount(42)],
        integration_by_acct={42: integration},
    )

    await ingestor._for_each_account_with_capability(
        Capability.VEHICLE_STATE, _job,
    )
    assert ran_for == []


@pytest.mark.asyncio
async def test_fan_out_defaults_to_running_when_no_integration_row(monkeypatch):
    """An account WITHOUT an integration row still runs — preserves
    pre-migration behaviour during M3 rollout."""
    from capabilities.telemetry import ingestor

    ran_for: list[int] = []

    async def _job(account_id: int) -> int:
        ran_for.append(account_id)
        return 0

    _patch_fan_out_dependencies(
        monkeypatch,
        accounts=[_StubAccount(42)],
        integration_by_acct={},  # no row for 42
    )

    await ingestor._for_each_account_with_capability(
        Capability.VEHICLE_STATE, _job,
    )
    assert ran_for == [42]


@pytest.mark.asyncio
async def test_fan_out_defaults_to_running_when_capability_unknown(monkeypatch):
    """An integration row whose toggle map predates a new capability
    should default that capability to enabled — never silently
    disabled because of timing."""
    from capabilities.telemetry import ingestor

    ran_for: list[int] = []

    async def _job(account_id: int) -> int:
        ran_for.append(account_id)
        return 0

    integration = _fake_integration(
        account_id=42,
        # Toggle map mentions other capabilities but not VEHICLE_STATE.
        toggles={Capability.SAFETY_EVENTS: {"enabled": False}},
    )

    _patch_fan_out_dependencies(
        monkeypatch,
        accounts=[_StubAccount(42)],
        integration_by_acct={42: integration},
    )

    await ingestor._for_each_account_with_capability(
        Capability.VEHICLE_STATE, _job,
    )
    assert ran_for == [42]


@pytest.mark.asyncio
async def test_fan_out_runs_per_account_independently(monkeypatch):
    """Per-account toggles fan out correctly across mixed configurations."""
    from capabilities.telemetry import ingestor

    ran_for: list[int] = []

    async def _job(account_id: int) -> int:
        ran_for.append(account_id)
        return 0

    integrations = {
        1: _fake_integration(
            account_id=1,
            toggles={Capability.VEHICLE_STATE: {"enabled": True}},
        ),
        2: _fake_integration(
            account_id=2,
            toggles={Capability.VEHICLE_STATE: {"enabled": False}},
        ),
        # 3 has no row at all — defaults to running.
    }

    _patch_fan_out_dependencies(
        monkeypatch,
        accounts=[_StubAccount(1), _StubAccount(2), _StubAccount(3)],
        integration_by_acct=integrations,
    )

    await ingestor._for_each_account_with_capability(
        Capability.VEHICLE_STATE, _job,
    )
    # Order is non-deterministic across concurrent fan-out — compare sets.
    assert set(ran_for) == {1, 3}


# ── Test plumbing ────────────────────────────────────────────────


def _patch_fan_out_dependencies(monkeypatch, *, accounts, integration_by_acct):
    """Stub out the platform DB + tenant DB + RLS context manager so
    the fan-out logic runs in isolation without touching Postgres."""
    from capabilities.telemetry import ingestor

    class _StubPlatformDB:
        async def list_accounts(self, active_only=True):
            return list(accounts)

        async def get_account_integration(self, account_id, provider_id):
            return integration_by_acct.get(account_id)

    class _StubTenantContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return None

    class _StubTenantDB:
        def with_account(self, account_id):
            return _StubTenantContext()

    stub_platform = _StubPlatformDB()
    monkeypatch.setattr(ingestor, "get_platform_db", lambda: stub_platform)
    monkeypatch.setattr(
        ingestor, "get_tenant_db", AsyncMock(return_value=_StubTenantDB()),
    )
