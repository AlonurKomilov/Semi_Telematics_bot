"""Per-request tenant context — ContextVars for COMPANY_DISPLAY / ORG_IDS.

Replaces the module-level global dicts in samsara_client.py with
per-async-task scoped dictionaries.  Each Telegram handler or scheduled
job sets the vars before doing any work; formatters and reports read
them transparently.

Usage at handler entry:
    set_tenant_display(tenant.company_display, tenant.org_ids)

Usage in formatters / reports / anywhere:
    from core.context import get_company_display, get_org_ids
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
