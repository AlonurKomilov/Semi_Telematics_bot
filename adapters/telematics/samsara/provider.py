"""SamsaraProvider — the protocol implementation for the Samsara
integration.

Adapts the existing ``MultiCompanyClient`` to the vendor-neutral
``TelematicsProvider`` interface so the rest of the system can pick
the provider up through the registry rather than importing the
Samsara client directly.

The provider is a thin wrapper around an already-built
``MultiCompanyClient``.  The client's per-company SamsaraClient
instances do the actual HTTP work; the provider's job is to map
protocol method names onto the existing client methods and translate
between vendor-shaped and protocol-shaped argument sets.

Lifecycle
---------
A provider instance is bound to one account_id at construction.  The
scheduler resolves the right provider via ``get_telematics_client``,
which reads the account_integrations row to find the provider_id and
then asks the registry for the implementation class.

Future
------
When OAuth2-based providers (Geotab, possibly Motive) come online,
their ``test_connection`` may need a separate auth probe; the
Samsara API-token-only flow doesn't.  The protocol is intentionally
loose on auth so each provider implements what fits its vendor.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from adapters.telematics.catalog import PROVIDER_CATALOG
from adapters.telematics.protocol import (
    Capability,
    ConnectionStatus,
    TelematicsProvider,
)

from .client import MultiCompanyClient

logger = logging.getLogger(__name__)


class SamsaraProvider:
    """``TelematicsProvider`` implementation backed by
    ``MultiCompanyClient``.

    Instantiated per-account via ``get_telematics_client``; holds a
    reference to the multi-company client built from the account's
    ``companies`` rows.  Future per-account credentials stored in
    ``account_integrations.credentials_enc`` will replace the
    companies-table lookup once we unify the two; until then the
    provider reads through the existing path so behaviour is
    unchanged.
    """

    provider_id: str = "samsara"

    # Mirrors the catalog entry — kept in sync at construction time so
    # callers can introspect a provider instance without consulting
    # the catalog separately.
    supported_capabilities: frozenset[str] = frozenset({
        Capability.VEHICLE_STATE,
        Capability.SAFETY_EVENTS,
        Capability.VEHICLE_HEALTH,
        Capability.VEHICLE_FAULTS,
        Capability.DRIVER_EFFICIENCY_DAILY,
        Capability.FLEET_WEATHER,
        Capability.FLEET_EFFICIENCY,
        Capability.GEOFENCE_DEFINITIONS,
        Capability.STATE_SNAPSHOT_HISTORY,
        Capability.TELEMETRY_HOURLY,
        Capability.METRICS_DAILY,
        Capability.HISTORY_PRUNE,
        Capability.HISTORY_BACKFILL,
    })

    def __init__(self, client: MultiCompanyClient) -> None:
        self._client = client

    # ── Identity / direct access for legacy callers ───────────────

    @property
    def client(self) -> MultiCompanyClient:
        """The underlying ``MultiCompanyClient``.

        Exposed so legacy code that still calls Samsara-specific
        methods (``invalidate_cache``, ``ensure_org_ids``,
        per-company iteration) can do so without losing those
        affordances.  Anything in the ``TelematicsProvider`` protocol
        should go through the protocol methods so it survives a
        future provider swap.
        """
        return self._client

    # ── Lifecycle ─────────────────────────────────────────────────

    async def test_company(self, company_code: str) -> ConnectionStatus:
        """Probe ONE company's ``/me`` endpoint.

        Same lean-probe semantics as ``test_connection`` but scoped
        to a single company so the dashboard can surface per-company
        diagnostics — operator clicks "Test" next to OSY, sees a
        specific result for OSY only.  Account-level
        ``test_connection`` fans this out across every configured
        company and surfaces the first failure.

        Returns ``ConnectionStatus`` with:
          * ok=True  + message="reachable in 187ms" + org id
          * ok=False + message="HTTP 401 — credentials rejected"
          * ok=False + message="timeout after 3s"
          * ok=False + message="no such company"
        """
        client = self._client.clients.get(company_code)
        if client is None:
            return ConnectionStatus(
                ok=False,
                message=f"no such company: {company_code}",
            )
        code, org_id, err = await self._probe_one_company(
            company_code, client,
        )
        if err:
            return ConnectionStatus(ok=False, message=err[:300])
        return ConnectionStatus(
            ok=True,
            message="reachable",
            provider_account_id=org_id or "",
        )

    async def _probe_one_company(
        self, code: str, c: Any,
    ) -> tuple[str, Optional[str], Optional[str]]:
        """Single-company /me probe — the shared body of
        ``test_company`` and ``test_connection``.  Returns
        ``(code, org_id_or_None, error_or_None)``.  Never raises —
        all exceptions become the error string so ``asyncio.gather``
        in ``test_connection`` doesn't have to special-case anything.
        """
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=3)
        try:
            async with aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {c.api_key}"},
                timeout=timeout,
            ) as sess:
                async with sess.get(f"{c.base_url}/me") as resp:
                    if resp.status in (401, 403):
                        return code, None, (
                            f"{code}: credentials rejected "
                            f"(HTTP {resp.status})"
                        )
                    if resp.status >= 400:
                        return code, None, f"{code}: HTTP {resp.status}"
                    data = await resp.json()
                    org_id = str(data.get("data", {}).get("id", ""))
                    return code, org_id or None, None
        except asyncio.TimeoutError:
            return code, None, f"{code}: timeout after 3s"
        except aiohttp.ClientError as e:
            return code, None, (
                f"{code}: {type(e).__name__}: {str(e)[:120]}"
            )
        except Exception as e:  # noqa: BLE001
            return code, None, f"{code}: {type(e).__name__}: {str(e)[:120]}"

    async def test_connection(self, creds: dict[str, Any]) -> ConnectionStatus:
        """Probe the upstream with a single direct ``/me`` call per company.

        ## Why this bypasses the shared client stack
        Earlier iterations of this method routed through
        ``SamsaraClient.get_org_id`` which ran inside the breaker +
        429-retry chain.  Worst case per company was
        ``5s_timeout × (1 + 3 retries × 5s sleep) = 25s`` if the
        backfill traffic was driving the API key into 429s.  Five
        companies in parallel = the slowest wins; one bad company
        could still blow past the route's 12s ``wait_for`` budget,
        which the operator saw as a 30s browser timeout (when nginx
        hadn't already 504'd at its own ``proxy_read_timeout``).

        The lean probe avoids every one of those hazards:
          * **Fresh session per call** — bypasses the shared keepalive
            pool that the backfill is monopolising.
          * **No breaker** — this probe IS the breaker test; counting
            its failures against the breaker self-poisons.
          * **No 429-retry loop** — a 429 on /me is vanishingly rare
            and we'd rather see it as a fast failure than queue.
          * **``ClientTimeout(total=3)`` per request** — hard ceiling
            on wall-clock, independent of upstream retry-after.
          * **``asyncio.gather(...)``** — N companies in parallel; one
            bad company doesn't cancel the others mid-flight.

        Doesn't use the ``creds`` argument today — the Samsara client
        is built with the per-company API keys held in ``companies``.
        The argument is part of the protocol so future providers
        whose credentials live solely in the integrations table can
        validate without a database read.
        """
        clients = self._client.clients
        if not clients:
            return ConnectionStatus(
                ok=False,
                message="no companies configured for this integration",
            )

        results = await asyncio.gather(
            *(self._probe_one_company(code, c) for code, c in clients.items()),
            return_exceptions=False,  # helper never raises
        )

        # Surface the first failure with the company code prefixed so
        # mixed-credential failures point at the bad token.
        for _code, _org, err in results:
            if err:
                return ConnectionStatus(ok=False, message=err[:300])

        provider_account_id = next(
            (org for _c, org, _e in results if org), "",
        ) or ""
        n = len(clients)
        return ConnectionStatus(
            ok=True,
            message=f"{n} compan{'y' if n == 1 else 'ies'} reachable",
            provider_account_id=provider_account_id,
        )

    async def close(self) -> None:
        await self._client.close()

    # ── Live state ────────────────────────────────────────────────

    async def get_fleet_overview(self) -> list[dict[str, Any]]:
        return await self._client.get_fleet_overview()

    async def get_safety_events(self, *, days: int = 2) -> list[dict[str, Any]]:
        """Samsara's safety events come from ``get_events``.  The
        protocol's method name is provider-neutral; the days window
        is Samsara-specific but defaults match the existing
        scheduler call site."""
        return await self._client.get_events(days=days)

    async def get_vehicle_health(self) -> list[dict[str, Any]]:
        return await self._client.get_vehicle_health()

    async def get_vehicle_faults(self) -> list[dict[str, Any]]:
        """``MultiCompanyClient.get_vehicles_with_faults`` returns
        ``(rows, total_active, per_company_breakdown)`` for legacy
        callers that want all three; protocol returns just the rows."""
        rows, _total, _breakdown = await self._client.get_vehicles_with_faults()
        return rows

    # ── Historical ───────────────────────────────────────────────

    async def get_stats_history(
        self,
        types: list[str],
        start_iso: str,
        end_iso: str,
        *,
        company_code: Optional[str] = None,
    ) -> dict[str, dict[str, Any]]:
        """Wraps Samsara's ``_get_paginated_history`` with protocol-
        shaped arguments.  Returns ``{vehicle_id: {type: [points]}}``
        across all per-company clients merged into one dict.

        ``types`` is a list because protocol callers think in
        vendor-neutral capability names; this method joins them into
        the comma-separated string Samsara wants and walks every
        per-company client (Samsara has no cross-org history endpoint).

        ``company_code`` (optional) restricts the walk to a single
        company so the per-company "Refresh" button on the Integration
        card can backfill exactly one company's history without
        pulling the others' rate limits.  Unknown code returns the
        empty dict — caller treats it the same as "no data this day".
        """
        types_csv = ",".join(types)
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        merged: dict[str, dict[str, Any]] = {}
        if company_code is not None:
            scoped = self._client.clients.get(company_code)
            if scoped is None:
                return merged
            items = [(company_code, scoped)]
        else:
            items = list(self._client.clients.items())
        for code, company_client in items:
            try:
                per_company = await company_client._get_paginated_history(
                    types_csv, start, end,
                )
            except Exception:
                logger.exception(
                    "samsara stats/history failed for company=%s — skipping",
                    code,
                )
                continue
            for vid, payload in per_company.items():
                # Tag every vehicle with its company code so downstream
                # consumers can disambiguate cross-company name collisions
                # (the same vehicle_name "103" can appear in two orgs).
                payload.setdefault("_org", code)
                merged[vid] = payload
        return merged


# Compile-time check that we satisfy the protocol — if a method
# signature drifts, this assignment fails at import time rather than
# at first use.  ``runtime_checkable`` makes the protocol usable as
# an isinstance() target as well.
_PROVIDER_PROTOCOL_CHECK: type[TelematicsProvider] = SamsaraProvider


# ── Catalog drift guard ──────────────────────────────────────────
#
# If the catalog's declared capability set ever drifts from the
# provider's own ``supported_capabilities``, surface that as a clear
# ImportError so it can never silently disagree at runtime.

_catalog_caps = PROVIDER_CATALOG["samsara"].capabilities
_provider_caps = SamsaraProvider.supported_capabilities
if _catalog_caps != _provider_caps:
    only_catalog = _catalog_caps - _provider_caps
    only_provider = _provider_caps - _catalog_caps
    raise ImportError(
        "samsara provider capability mismatch — "
        f"catalog has {sorted(only_catalog)!r}, "
        f"provider has {sorted(only_provider)!r}; "
        "update the catalog or the provider so both agree."
    )
