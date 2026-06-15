"""DatatruckProvider — TMS provider exposed through the
``TelematicsProvider`` protocol.

Datatruck is a Transportation Management System, not a telematics
platform — but for UX consistency the dashboard renders it as just
another Integration card alongside Samsara/Motive/Geotab.  The shared
protocol means the dashboard rendering, the test-connection route,
the per-account credential storage, and the integration health
recorder all work unchanged.

The telematics methods (``get_vehicles_overview``, ``get_safety_events``,
``get_vehicle_health``, ``get_vehicle_faults``, ``get_stats_history``)
return empty results — Datatruck's API doesn't expose them, and the
catalog doesn't claim them in ``supported_capabilities``, so the
scheduler will never call them in practice.  They're still implemented
as cheap no-ops so the runtime protocol check passes.

The TMS-shaped methods (drivers / trucks / trailers / orders / work-
orders) carry the real data and are exercised by capability jobs that
key off ``supported_capabilities``.
"""

from __future__ import annotations

import logging
from typing import Any

from adapters.telematics.catalog import PROVIDER_CATALOG
from adapters.telematics.protocol import (
    Capability,
    ConnectionStatus,
    TelematicsProvider,
)

from .client import DatatruckClient

logger = logging.getLogger(__name__)


class DatatruckProvider:
    """``TelematicsProvider`` implementation for the Datatruck TMS."""

    provider_id: str = "datatruck"

    supported_capabilities: frozenset[str] = frozenset({
        Capability.TMS_DRIVERS_SYNC,
        Capability.TMS_TRUCKS_SYNC,
        Capability.TMS_TRAILERS_SYNC,
        Capability.TMS_ORDERS_SYNC,
        Capability.TMS_WORK_ORDERS_SYNC,
    })

    def __init__(self, client: DatatruckClient) -> None:
        self._client = client
        # Mark whether this instance was built via build_for_test —
        # in that case close_if_owned_by_test() releases the single-use
        # HTTPS client.  The cached resolver path leaves it False.
        self._owned_by_test = False

    @property
    def client(self) -> DatatruckClient:
        return self._client

    @classmethod
    async def build_for_test(
        cls, account_id: int, creds: dict[str, Any],
    ) -> "DatatruckProvider":
        """Construct a single-use provider from raw credentials for
        the shared connect route's test probe.

        Datatruck's per-tenant URL means we can't go through the
        cached resolver path (which reads creds from the integration
        row, which doesn't exist yet on first connect).  Build a bare
        ``DatatruckClient`` from the subdomain + token in ``creds``,
        wrap it, and tag it as test-owned so the shared route's
        ``close_if_owned_by_test()`` releases the aiohttp session
        after the probe.
        """
        sub = (creds or {}).get("company_subdomain") or ""
        tok = (creds or {}).get("api_token") or ""
        if not sub or not tok:
            raise ValueError(
                "datatruck requires both 'company_subdomain' "
                "(e.g. 'premier') and 'api_token' in credentials",
            )
        client = DatatruckClient(company_subdomain=sub, api_token=tok)
        instance = cls(client)
        instance._owned_by_test = True
        return instance

    async def close_if_owned_by_test(self) -> None:
        """Release the single-use HTTPS client built by
        ``build_for_test``.  No-op when the instance was built any
        other way (cached resolver path)."""
        if self._owned_by_test:
            await self._client.close()

    # ── Lifecycle ─────────────────────────────────────────────────

    async def test_connection(self, creds: dict[str, Any]) -> ConnectionStatus:
        """Probe ``/drivers/list/?page_size=1`` with the configured
        credentials.  ``creds`` is the dict the dashboard sent on
        Connect — not used here because the client was already
        constructed with the persisted credentials.  Kept in the
        signature to satisfy the protocol shape.
        """
        ok, message, meta = await self._client.test_connection()
        if ok and isinstance(meta, dict) and meta.get("count") is not None:
            message = f"reachable — {meta['count']} drivers visible"
        return ConnectionStatus(
            ok=ok,
            message=message,
            # Datatruck doesn't expose an "org id" — use the subdomain
            # as the stable cross-reference operators can look up.
            provider_account_id=self._client.subdomain,
        )

    async def close(self) -> None:
        await self._client.close()

    # ── Telematics methods — no-op for a TMS ─────────────────────
    #
    # These return empty results.  Catalog declares zero telematics
    # capabilities for Datatruck, so the scheduler never invokes
    # them.  Implemented anyway so the runtime ``isinstance`` check
    # against the protocol passes and tests can mock-check shape.

    async def get_vehicles_overview(self) -> list[dict[str, Any]]:
        return []

    async def get_safety_events(self) -> list[dict[str, Any]]:
        return []

    async def get_vehicle_health(self) -> list[dict[str, Any]]:
        return []

    async def get_vehicle_faults(self) -> list[dict[str, Any]]:
        return []

    async def get_stats_history(
        self,
        types: list[str],
        start_iso: str,
        end_iso: str,
        *,
        company_code: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        return {}

    # ── TMS sync entry points ────────────────────────────────────
    #
    # First-page reads used by the dashboard's "Sync preview" surface
    # and by the future capability jobs.  Pagination across pages
    # lands in the capability layer, not here, so the dashboard's
    # one-click "show me what's there" stays under one request.

    async def fetch_drivers_page(self) -> dict[str, Any]:
        return await self._client.list_drivers()

    async def fetch_trucks_page(self) -> dict[str, Any]:
        return await self._client.list_trucks()

    async def fetch_trailers_page(self) -> dict[str, Any]:
        return await self._client.list_trailers()

    async def fetch_orders_page(self) -> dict[str, Any]:
        return await self._client.list_orders()

    async def fetch_work_orders_page(
        self, *, from_date: str, to_date: str,
    ) -> dict[str, Any]:
        return await self._client.list_work_orders(
            from_date=from_date, to_date=to_date,
        )


# Compile-time protocol satisfaction check.
_PROVIDER_PROTOCOL_CHECK: type[TelematicsProvider] = DatatruckProvider


# Catalog drift guard — refuse to import if the catalog and the
# provider disagree on capabilities.  Cheap insurance against
# accidentally adding a capability in one place but not the other.
_catalog_caps = PROVIDER_CATALOG["datatruck"].capabilities
_provider_caps = DatatruckProvider.supported_capabilities
if _catalog_caps != _provider_caps:
    only_catalog = _catalog_caps - _provider_caps
    only_provider = _provider_caps - _catalog_caps
    raise ImportError(
        "datatruck provider capability mismatch — "
        f"catalog has {sorted(only_catalog)!r}, "
        f"provider has {sorted(only_provider)!r}; "
        "update the catalog or the provider so both agree.",
    )
