"""Company-scope gate for alert DELIVERY (Telegram DMs).

A subscriber restricted to certain companies — via their per-user
assignment (Team Management → Company Access) — must NOT be DM'd about
another company's vehicle.  This is the delivery-side twin of the
dashboard's company filtering.

SSOT used by every per-user alert fan-out: the main pipeline, camera
reports, and hourly re-escalation.  Forum/group-topic delivery is NOT
gated here — a shared group topic is per-account, not per-user.

FAIL-OPEN by design (never silence a legitimate alert):
  • owner / unrestricted subscriber (empty company list) → gets everything,
  • an alert with no company (``co`` empty / unknown) → delivered to all,
  • any error loading the scope maps → no filtering (deliver to all).
"""
from __future__ import annotations

from adapters.storage import Role
from infra.platform import get_platform_db


async def load_company_scope(account_id: int) -> dict:
    """``user_id → [company codes]`` map for the account, or ``{}`` on error."""
    try:
        return await get_platform_db().get_all_user_company_codes(account_id)
    except Exception:
        return {}


def subscriber_companies(sub, user_co: dict) -> list:
    """A subscriber's allowed companies (``[]`` = unrestricted).

    From their per-user assignment (Team Management).  Owner is always
    unrestricted.
    """
    if sub.role == Role.OWNER:
        return []
    return user_co.get(sub.id, [])


def sees_company(sub, co: str, user_co: dict) -> bool:
    """True if *sub* may receive an alert for company ``co`` (fail-open)."""
    if not co:
        return True
    allowed = subscriber_companies(sub, user_co)
    if not allowed:
        return True
    return co.upper() in {c.upper() for c in allowed}


async def filter_subscribers_by_company(subscribers: list, co: str, account_id: int) -> list:
    """Drop subscribers whose company scope excludes single-company ``co``.

    For per-alert fan-outs (pipeline, re-escalation) where the whole alert
    is one company.  No-op (returns all) when ``co`` is empty, there are
    no subscribers, or no per-user company scoping is configured.
    """
    if not co or not subscribers:
        return subscribers
    user_co = await load_company_scope(account_id)
    if not user_co:
        return subscribers
    return [s for s in subscribers if sees_company(s, co, user_co)]
