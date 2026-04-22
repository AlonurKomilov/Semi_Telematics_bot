"""Vehicle catalog service — Single Source of Truth for fleet vehicle data.

Both bot handlers and API routes call these methods instead of
duplicating Samsara client interaction + company-display setup.
"""

from __future__ import annotations

from core.services import get_client, get_tenant_db
from adapters.samsara.client import populate_company_display


async def prepare_companies(account_id: str) -> list:
    """Load companies from tenant DB and populate display-name mapping.

    Returns the list of Company objects (with .code, .display_name).
    """
    tenant = await get_tenant_db(account_id)
    companies = await tenant.get_account_companies(account_id)
    populate_company_display(companies)
    return companies


async def get_company_codes(account_id: str) -> list[str]:
    """Return company codes for an account (after populating display names)."""
    companies = await prepare_companies(account_id)
    return [c.code for c in companies]


async def get_fleet_overview(
    account_id: str,
    company: str | None = None,
) -> list[dict]:
    """Fetch fleet overview vehicles, optionally filtered by company."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_fleet_overview(company=company)


async def get_vehicle_detail(
    account_id: str,
    truck_name: str,
    company: str | None = None,
) -> list[dict]:
    """Look up a single vehicle by name. Returns list of matches."""
    await prepare_companies(account_id)
    client = await get_client(account_id)
    return await client.get_vehicle_detail(truck_name, company=company)


async def get_critical_faults(
    account_id: str,
    company: str | None = None,
) -> tuple[list[dict], int, dict]:
    """Fetch critical-fault vehicles plus fleet totals.

    Returns (critical_vehicles, total_vehicle_count, company_breakdown).
    """
    await prepare_companies(account_id)
    client = await get_client(account_id)
    critical = await client.get_critical_faults(company=company)
    _, total, breakdown = await client.get_vehicles_with_faults(company=company)
    return critical, total, breakdown
