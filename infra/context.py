"""Per-request tenant context — ContextVars for COMPANY_DISPLAY / ORG_IDS.

Replaces the module-level global dicts in samsara_client.py with
per-async-task scoped dictionaries.  Each Telegram handler or scheduled
job sets the vars before doing any work; formatters and reports read
them transparently.

Usage at handler entry:
    set_tenant_display(tenant.company_display, tenant.org_ids)

Usage in formatters / reports / anywhere:
    from infra.context import get_company_display, get_org_ids
    name = get_company_display().get(code, code)
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# ── Context variables ────────────────────────────────────────────
# Default values are ``None``; the getters fall back to the legacy
# module-level dicts in samsara_client so existing code works
# unchanged even before all callers are ported.

_company_display_var: ContextVar[dict[str, str] | None] = ContextVar(
    "company_display", default=None
)
_org_ids_var: ContextVar[dict[str, str] | None] = ContextVar(
    "org_ids", default=None
)

# ── prepare_companies idempotency guard ──────────────────────────
# Tracks which account_ids have already been prepared in the current
# async task so repeated calls to prepare_companies() become no-ops.
# We cache the companies list itself per-account so the second call
# can return it without re-hitting the DB — measurable since hot
# routes call prepare_companies 5-6 times per request via every signal
# fetcher (events, vehicle_health, fuel, fleet_efficiency, …).

_companies_cache_var: ContextVar[dict[int, list]] = ContextVar(
    "companies_cache", default={},
)


def is_companies_prepared(account_id: int) -> bool:
    """Return True if prepare_companies() was already called for this account
    in the current async task context."""
    return account_id in _companies_cache_var.get()


def get_cached_companies(account_id: int) -> list | None:
    """Return the cached companies list for *account_id* if any, else None.

    The list was stored by ``mark_companies_prepared`` on the first
    ``prepare_companies()`` call in this async task. Letting callers
    reuse it skips the DB roundtrip on repeat calls.
    """
    return _companies_cache_var.get().get(account_id)


def mark_companies_prepared(account_id: int, companies: list | None = None) -> None:
    """Record that prepare_companies() completed for *account_id* in this context.

    *companies* is the list returned by ``tenant.get_account_companies``
    so subsequent ``prepare_companies()`` calls in the same task can
    return it without another DB query. Passing ``None`` keeps the
    legacy "marker only" behaviour for callers that don't have the list
    handy.

    Creates a new dict copy so ContextVar isolation is maintained — each
    async task gets its own dict and changes don't bleed across tasks.
    """
    current = _companies_cache_var.get()
    new = {**current, account_id: companies if companies is not None else []}
    _companies_cache_var.set(new)


# ── Setters ──────────────────────────────────────────────────────

def set_tenant_display(
    company_display: dict[str, str],
    org_ids: dict[str, str] | None = None,
) -> None:
    """Set per-request company display names and org IDs.

    Call at the top of every handler / scheduled-job iteration before
    any formatter or report function is invoked.
    """
    _company_display_var.set(company_display)
    if org_ids is not None:
        _org_ids_var.set(org_ids)


# ── Getters ──────────────────────────────────────────────────────

def get_company_display() -> dict[str, str]:
    """Return the company display dict for the current async context.

    Returns an empty dict when no tenant-scoped dict has been set
    (callers should always call populate_company_display first).
    """
    val = _company_display_var.get()
    if val is not None:
        return val
    logger.warning(
        "get_company_display() called without set_tenant_display() — "
        "falling back to module-level COMPANY_DISPLAY (may be stale or wrong account)"
    )
    from adapters.samsara.client import COMPANY_DISPLAY
    return COMPANY_DISPLAY


def get_org_ids() -> dict[str, str]:
    """Return the org-IDs dict for the current async context.

    Returns an empty dict when no tenant-scoped dict has been set
    (callers should always call populate_company_display first).
    """
    val = _org_ids_var.get()
    if val is not None:
        return val
    logger.warning(
        "get_org_ids() called without set_tenant_display() — "
        "falling back to module-level ORG_IDS (may be stale or wrong account)"
    )
    from adapters.samsara.client import ORG_IDS
    return ORG_IDS
