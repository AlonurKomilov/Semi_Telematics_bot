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

from adapters.telematics.catalog import assert_declarations_agree
from adapters.telematics.protocol import (
    Capability,
    ConnectionStatus,
    DutyStatus,
    HosClock,
    HosSnapshot,
    TelematicsProvider,
)


# Samsara's duty-status spellings → ours.  Keys are NORMALISED (lower
# case, letters and digits only) so a rename between ``sleeperBerth``
# and ``sleeper_berth`` costs nothing, and several accepted spellings
# per value cost one line each.
#
# Anything not in this table becomes UNKNOWN, deliberately.  The
# tempting default is OFF_DUTY — it is the commonest real value — and
# it is the one answer we must never invent: it would turn a gap in
# THIS table into a statement that a driver was resting.
_DUTY_BY_VENDOR_SPELLING = {
    "offduty":             DutyStatus.OFF_DUTY,
    "off":                 DutyStatus.OFF_DUTY,
    "sleeperberth":        DutyStatus.SLEEPER,
    "sleeperbed":          DutyStatus.SLEEPER,
    "sleeper":             DutyStatus.SLEEPER,
    "driving":             DutyStatus.DRIVING,
    "drive":               DutyStatus.DRIVING,
    "onduty":              DutyStatus.ON_DUTY,
    "on":                  DutyStatus.ON_DUTY,
    "personalconveyance":  DutyStatus.PERSONAL_CONVEYANCE,
    "pc":                  DutyStatus.PERSONAL_CONVEYANCE,
    "yardmove":            DutyStatus.YARD_MOVE,
    "ym":                  DutyStatus.YARD_MOVE,
}


def _duty_status(raw) -> str:
    """One vendor spelling → one :class:`DutyStatus` value."""
    key = "".join(ch for ch in str(raw or "").lower() if ch.isalnum())
    return _DUTY_BY_VENDOR_SPELLING.get(key, DutyStatus.UNKNOWN)

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
        Capability.DRIVER_HOS,
        # The snapshot/hourly/daily roll-ups are NOT advertised here — they're
        # provider-agnostic warehouse plumbing (always-on), not a Samsara
        # capability the owner toggles.  See adapters/telematics/catalog.
        # HISTORY_PRUNE retired — retention is owned by the Retention hub
        # (data_retention job + operator Retention page), not a per-account
        # integration toggle.  See catalog._SAMSARA_DEFAULTS.
        Capability.HISTORY_BACKFILL,
    })

    # Samsara reports all four countdowns.  Declared rather than
    # inferred from a payload, so a mapping regression that silently
    # produced None would fail the shared guard instead of quietly
    # removing a column from Hours of Service.
    hos_clocks_reported: frozenset[str] = HosClock.ALL

    def __init__(self, client: MultiCompanyClient) -> None:
        self._client = client
        # Flag the test-build path so close_if_owned_by_test() knows
        # whether to drop the underlying client.  Samsara's build_for_test
        # routes through the cached resolver so this stays False — the
        # cached MultiCompanyClient lives across requests and must not
        # be closed after a single connect probe.
        self._owned_by_test = False

    @classmethod
    async def build_for_test(
        cls, account_id: int, creds: dict[str, Any],
    ) -> "SamsaraProvider":
        """Construct a provider instance for the shared connect-route's
        test probe.

        For Samsara we route through the cached telematics-client
        resolver since the underlying ``MultiCompanyClient`` is built
        from per-company keys already on the ``companies`` table —
        ``creds`` from the connect form is the integration credentials
        envelope, not per-company tokens.  The cached instance survives
        across the test → upsert → first-ingest path so we don't
        rebuild it three times in a row.
        """
        from infra.services import get_telematics_client
        provider = await get_telematics_client(
            account_id, "samsara", prefetch=False,
        )
        return provider  # type: ignore[return-value]

    async def close_if_owned_by_test(self) -> None:
        """No-op for Samsara — the test path returns the cached
        instance, which must NOT be closed after a probe.  Datatruck
        overrides this to release its single-use HTTPS session."""
        return None

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
            # Brand-new account path: the owner connects Samsara BEFORE
            # adding any companies (or before any company has a key).
            # The per-company fan-out below would have nothing to probe,
            # and the old behaviour — "credentials rejected: no companies
            # configured" — read like the token was bad when it was
            # never even tested.  Instead, probe the connect-form token
            # directly against /me so a valid token connects cleanly and
            # the success message steers the owner to the next step.
            # The token persists as ``credentials.api_token`` which
            # ``build_multi_company_client`` uses as the fallback for
            # companies without their own key — so a single-company
            # account is fully live the moment they add the company.
            token = str((creds or {}).get("api_token") or "").strip()
            if not token:
                return ConnectionStatus(
                    ok=False,
                    message=(
                        "no companies with API keys yet — add your "
                        "companies on the Companies page, then set each "
                        "one's key under Connected companies on this card"
                    ),
                )
            from types import SimpleNamespace
            probe_target = SimpleNamespace(
                api_key=token, base_url="https://api.samsara.com",
            )
            _code, org_id, err = await self._probe_one_company(
                "token", probe_target,
            )
            if err:
                return ConnectionStatus(ok=False, message=err[:300])
            return ConnectionStatus(
                ok=True,
                message=(
                    "API token valid — next, add your companies on the "
                    "Companies page; this token covers any company "
                    "without its own key"
                ),
                provider_account_id=org_id or "",
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

    async def get_vehicles_overview(self) -> list[dict[str, Any]]:
        return await self._client.get_vehicles_overview()

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

    async def get_driver_hos(self) -> list[HosSnapshot]:
        """Samsara's duty clocks, in OUR vocabulary.

        This is where the vendor's spelling stops.  Everything above
        this line sees :class:`DutyStatus` values and seconds; nothing
        above it has ever heard of ``hosStatusType``.

        ``source_ts`` is stamped at fetch time, and for this endpoint
        that IS the observation time — ``/fleet/hos/clocks`` answers
        "right now", not a historical window.  A provider that reports
        its own observation time should carry that instead.

        The capability is still not declared in
        ``supported_capabilities``; the ingest that consumes this lands
        with the declaration, so the toggle and the feed appear on the
        integration card together.
        """
        from datetime import datetime, timezone

        rows = await self._client.get_hos_clocks()
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return [
            HosSnapshot(
                provider_driver_id=str(r.get("provider_driver_id") or ""),
                duty_status=_duty_status(r.get("raw_duty_status")),
                drive_remaining_seconds=r.get("drive_remaining_seconds"),
                shift_remaining_seconds=r.get("shift_remaining_seconds"),
                cycle_remaining_seconds=r.get("cycle_remaining_seconds"),
                break_in_seconds=r.get("break_in_seconds"),
                last_status_change=str(r.get("last_status_change") or ""),
                source_ts=fetched_at,
                driver_name=str(r.get("driver_name") or ""),
                # The client already tags every row with the company it
                # came from (``_org``, OUR company code) and this line
                # was throwing it away.  Harmless on a one-company
                # account, and the whole problem on five.
                company_code=str(r.get("_org") or ""),
            )
            for r in rows
            if str(r.get("provider_driver_id") or "").strip()
        ]

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


# ── Declaration drift guard ──────────────────────────────────────
#
# Capability set must match the catalog, and the declared HOS clocks
# must be a legal subset.  Shared with every provider so the invariant
# is written once — see adapters/telematics/catalog.py.

assert_declarations_agree(SamsaraProvider)
