"""Vehicle catalog service — Single Source of Truth for fleet vehicle data.

Both bot handlers and API routes call these methods instead of
duplicating Samsara client interaction + company-display setup.

Reads route through the per-tenant warehouse (vehicle_state table) when
``WAREHOUSE_READS_ENABLED=1``; otherwise fall back to live Samsara.
The ingestor (capabilities/telemetry/ingestor.py) keeps the warehouse
fresh on a 60s cadence.
"""

from __future__ import annotations

from infra.context import (
    get_cached_companies, is_companies_prepared, mark_companies_prepared,
)
from infra.services import get_client, get_tenant_db
from adapters.samsara.client import populate_company_display


async def prepare_companies(account_id: int) -> list:
    """Load companies from tenant DB and populate display-name mapping.

    Returns the list of Company objects (with .code, .display_name).

    Idempotent within the current async-task context: subsequent calls
    for the same *account_id* return the cached list **without a DB
    round-trip**. Hot scoring requests call this 5-6 times via each
    signal fetcher; the cache cuts that to one query per request.
    """
    if is_companies_prepared(account_id):
        cached = get_cached_companies(account_id)
        if cached is not None:
            return cached
    tenant = await get_tenant_db(account_id)
    companies = await tenant.get_account_companies(account_id)
    populate_company_display(companies)
    mark_companies_prepared(account_id, companies)
    return companies


async def get_company_codes(account_id: int) -> list[str]:
    """Return company codes for an account (after populating display names)."""
    companies = await prepare_companies(account_id)
    return [c.code for c in companies]


async def get_fleet_overview(
    account_id: int,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview vehicles, optionally filtered by company.

    Warehouse-first: reads ``vehicle_state`` (60s-fresh) when
    WAREHOUSE_READS_ENABLED=1, falls back to live Samsara otherwise
    (or on cold-start empty warehouse).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)

    async def _live():
        return await client.get_fleet_overview(company=company)

    # Lazy import avoids circular dependency
    # (warehouse_reader → telemetry.service → vehicles.service)
    from capabilities.telemetry import warehouse_reader as _wh
    live = await _wh.get_current_vehicles(
        account_id, company=company, samsara_fallback=_live,
    )

    # Registry overlay — the Vehicle registry in our DB is the single
    # source of truth; Samsara/live state ENRICHES it.  Every active
    # registry vehicle shows (including trailers + manual trucks with
    # no telematics); live data is matched onto them.  When the
    # account has no registry rows yet (un-backfilled edge case) we
    # return the live list unchanged — zero behaviour change until the
    # migration-105 backfill / ingestor populates the registry.
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return live
    try:
        registry = await tenant.list_vehicles(
            account_id, company_code=company,
        )
    except Exception:
        return live
    if not registry:
        return live
    return _wh.merge_registry_with_live(registry, live)


async def get_vehicle_detail(
    account_id: int,
    vehicle_name: str,
    company: str | None = None,
) -> list[dict]:
    """Look up a single vehicle by name. Returns list of matches."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_vehicle_detail(vehicle_name, company=company)



