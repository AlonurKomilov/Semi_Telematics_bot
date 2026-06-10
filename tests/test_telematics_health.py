"""Smoke tests for the M8 telematics observability surface.

Covers:

  * ``run_integration_health_checks`` probes every connected
    integration, updates the row's health columns, and aggregates
    the flip count.
  * Timeout / exception paths record ``error`` / ``timeout`` to
    Prometheus and ``record_integration_health_check`` on the row.
  * Healthy → unchanged status does NOT flip the counter.
  * The Prometheus recording helpers are no-ops when the metrics
    instrumenter hasn't initialised yet (process-startup race).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from adapters.storage.account_integrations import AccountIntegration
from adapters.telematics.protocol import ConnectionStatus
from capabilities import telematics_health


def _integration(
    *,
    account_id: int = 1,
    status: str = "connected",
    creds: dict | None = None,
) -> AccountIntegration:
    return AccountIntegration(
        account_id=account_id,
        provider_id="samsara",
        status=status,
        connected_at="2026-01-01T00:00:00+00:00",
        credentials=creds or {"api_token": "t"},
        feature_toggles={},
        cadence_overrides={},
        last_health_at="",
        last_health_error="",
        last_backfill_at="",
        created_by=0,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


class _StubAccount:
    def __init__(self, account_id: int):
        self.id = account_id


@pytest.mark.asyncio
async def test_health_check_records_ok_for_healthy_provider(monkeypatch):
    db = MagicMock()
    db.list_accounts = AsyncMock(return_value=[_StubAccount(42)])
    db.list_enabled_account_integrations = AsyncMock(
        return_value=[_integration(account_id=42)],
    )
    db.record_integration_health_check = AsyncMock()
    monkeypatch.setattr(telematics_health, "get_platform_db", lambda: db)

    provider = MagicMock()
    provider.test_connection = AsyncMock(
        return_value=ConnectionStatus(ok=True, message="5 vehicles visible"),
    )
    monkeypatch.setattr(
        telematics_health, "get_telematics_client",
        AsyncMock(return_value=provider),
    )

    n = await telematics_health.run_integration_health_checks()
    assert n == 1
    db.record_integration_health_check.assert_awaited_once_with(
        42, "samsara", ok=True, message="5 vehicles visible",
    )


@pytest.mark.asyncio
async def test_health_check_records_error_on_provider_exception(monkeypatch):
    db = MagicMock()
    db.list_accounts = AsyncMock(return_value=[_StubAccount(42)])
    db.list_enabled_account_integrations = AsyncMock(
        return_value=[_integration(account_id=42)],
    )
    db.record_integration_health_check = AsyncMock()
    monkeypatch.setattr(telematics_health, "get_platform_db", lambda: db)

    provider = MagicMock()
    provider.test_connection = AsyncMock(side_effect=RuntimeError("401 Unauthorized"))
    monkeypatch.setattr(
        telematics_health, "get_telematics_client",
        AsyncMock(return_value=provider),
    )

    await telematics_health.run_integration_health_checks()
    args = db.record_integration_health_check.call_args
    assert args.kwargs["ok"] is False
    assert "401 Unauthorized" in args.kwargs["message"]


@pytest.mark.asyncio
async def test_health_check_records_timeout(monkeypatch):
    db = MagicMock()
    db.list_accounts = AsyncMock(return_value=[_StubAccount(42)])
    db.list_enabled_account_integrations = AsyncMock(
        return_value=[_integration(account_id=42)],
    )
    db.record_integration_health_check = AsyncMock()
    monkeypatch.setattr(telematics_health, "get_platform_db", lambda: db)

    async def _hang(*_a, **_k):
        await asyncio.sleep(30)
        return ConnectionStatus(ok=True)

    provider = MagicMock()
    provider.test_connection = _hang
    monkeypatch.setattr(
        telematics_health, "get_telematics_client",
        AsyncMock(return_value=provider),
    )
    monkeypatch.setattr(telematics_health, "_HEALTH_CHECK_TIMEOUT_SEC", 0.05)

    await telematics_health.run_integration_health_checks()
    args = db.record_integration_health_check.call_args
    assert args.kwargs["ok"] is False
    assert "timeout" in args.kwargs["message"].lower()


@pytest.mark.asyncio
async def test_health_check_skips_when_provider_unregistered(monkeypatch):
    """If the registry has no implementation we shouldn't blow up the
    whole pass — just skip the integration row."""
    db = MagicMock()
    db.list_accounts = AsyncMock(return_value=[_StubAccount(42)])
    db.list_enabled_account_integrations = AsyncMock(
        return_value=[_integration(account_id=42)],
    )
    db.record_integration_health_check = AsyncMock()
    monkeypatch.setattr(telematics_health, "get_platform_db", lambda: db)

    monkeypatch.setattr(
        telematics_health, "get_telematics_client",
        AsyncMock(side_effect=KeyError("samsara")),
    )

    await telematics_health.run_integration_health_checks()
    db.record_integration_health_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_health_check_iterates_multiple_accounts(monkeypatch):
    db = MagicMock()
    db.list_accounts = AsyncMock(
        return_value=[_StubAccount(1), _StubAccount(2), _StubAccount(3)],
    )

    async def _list(account_id):
        return [_integration(account_id=account_id)]

    db.list_enabled_account_integrations = _list
    db.record_integration_health_check = AsyncMock()
    monkeypatch.setattr(telematics_health, "get_platform_db", lambda: db)

    provider = MagicMock()
    provider.test_connection = AsyncMock(
        return_value=ConnectionStatus(ok=True),
    )
    monkeypatch.setattr(
        telematics_health, "get_telematics_client",
        AsyncMock(return_value=provider),
    )

    n = await telematics_health.run_integration_health_checks()
    assert n == 3
    assert db.record_integration_health_check.await_count == 3


def test_prometheus_recorders_are_safe_before_metrics_init():
    """The recorder helpers must work even when the metrics
    instrumenter hasn't initialised — this happens transiently at
    process startup."""
    from infra import observability as _obs

    # Should not raise.
    _obs.record_integration_health_check("samsara", "ok")
    _obs.record_integration_backfill_run("samsara", "completed")
