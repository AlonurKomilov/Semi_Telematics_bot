"""Dispatch section — IO wrappers.

Reads loads through ``features.loads.service`` (service-contract rule)
and hands them to the pure functions in ``grades.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from features.kpi.dispatch.grades import compute_dispatcher_kpis
from features.kpi.service import DEFAULT_KPI_THRESHOLDS, get_kpi_thresholds
from features.loads import service as loads_service
from infra.platform import get_tenant_db



async def get_dispatcher_kpis(
    account_id: int, *, days: int = 30,
    company_codes: list[str] | None = None,
) -> dict:
    """The Dispatcher KPI section: metrics + grades over the last ``days``
    of loads (manual + synced alike — the canonical model is the point).
    ``limit=None`` is load-bearing: the list default caps at 500 rows,
    which would silently understate every total on a busy account."""
    since = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).date().isoformat()
    loads = await loads_service.get_loads(
        account_id, since=since, company_codes=company_codes, limit=None,
    )
    # OFF-LOAD COSTS ARE UNATTRIBUTABLE UNDER A COMPANY RESTRICTION.
    #
    # These are the no-load rows (layover): ``load_id IS NULL``, so there
    # is no load to carry a ``company_code``, and ``load_line_items`` has
    # no company column of its own.  Nothing in the row says which
    # company the cost belongs to.
    #
    # They used to be fetched unconditionally while the loads beside them
    # were company-filtered, and that was wrong twice over:
    #
    #   * the merge loop uses ``groups.setdefault``, so an item for a
    #     dispatcher with no VISIBLE loads CREATED a row — a restricted
    #     viewer could see a dispatcher from a company outside their
    #     scope, rendered as "(user #123)" with costs and no revenue;
    #   * for dispatchers they could see, costs earned on other
    #     companies' work were charged in, inflating expenses, deflating
    #     gross, and moving the A–D grade.
    #
    # Unrestricted callers (``company_codes`` empty — owners, and anyone
    # with no per-user company assignment) keep the full picture.  A
    # restricted caller gets costs OMITTED rather than guessed: an
    # overstated gross is visible and explicable, a leaked dispatcher row
    # is neither.  Attributing these properly needs a company on the line
    # item, which is a schema change, not a filter.
    off_items = (
        [] if company_codes
        else await loads_service.get_off_load_line_items(
            account_id, since=since,
        )
    )
    tenant = await get_tenant_db(account_id)
    thresholds = (
        await get_kpi_thresholds(tenant, account_id)
        if tenant is not None else dict(DEFAULT_KPI_THRESHOLDS)
    )
    return {
        "days": days,
        "thresholds": thresholds,
        "dispatchers": compute_dispatcher_kpis(
            loads, thresholds, off_load_items=off_items,
        ),
    }
