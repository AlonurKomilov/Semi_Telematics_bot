"""Payroll service — orchestrates run lifecycle: create / finalize / cancel.

All calls write to the tenant audit log.  Respects the per-account
``payroll_enabled`` kill-switch.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from infra.services import get_db, get_tenant_db

from .engine import compute_run
from .models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_FINALIZED,
    VALID_KINDS,
    VALID_STATUSES,
    RunItem,
)

log = logging.getLogger(__name__)


class DriverPayDisabledError(RuntimeError):
    """Raised when driver-pay operations are attempted on an account where
    the ``payroll_enabled`` kill-switch is OFF."""


async def _assert_enabled(account_id: int) -> None:
    from capabilities.permissions.modules import module_enabled
    pdb = get_db()
    acct = await pdb.get_account(account_id)
    # Payroll is an Accounting feature (docs/FEATURES.md): available when
    # the Accounting module is on; who may use it is the can_driver_pay_admin
    # permission (masked off with the module).  No standalone switch.
    if acct is None or not module_enabled(
        getattr(acct, "disabled_modules", ""), "accounting",
    ):
        raise DriverPayDisabledError(
            f"accounting module is disabled for account {account_id}"
        )


async def _audit(
    account_id: int, user_id: int, action: str, target_id: str = "",
) -> None:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return
    try:
        await tenant.add_audit_log(
            account_id=account_id,
            user_id=user_id,
            action=action,
            target_type="driver_pay_run",
            target_id=target_id,
        )
    except Exception:  # pragma: no cover — audit must never break a run
        log.exception("driver-pay audit failed")


# ── Rule CRUD ──────────────────────────────────────────────────────

async def list_rules(account_id: int, *, active_only: bool = False) -> list[dict]:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.list_bonus_rules(account_id, active_only=active_only)


async def create_rule(
    account_id: int, *, user_id: int, name: str, kind: str,
    amount_cents: int, period_days: int = 30,
    score_min: Optional[float] = None,
    event_type: Optional[str] = None,
    max_count: Optional[int] = None,
    active: bool = True,
) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid rule kind {kind!r}")
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")
    if kind == KIND_SCORE_THRESHOLD and score_min is None:
        raise ValueError("score_threshold rule requires score_min")
    if kind == KIND_INCIDENT_COUNT and max_count is None:
        raise ValueError("incident_count rule requires max_count")

    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    rule_id = await tenant.create_bonus_rule(
        account_id, name=name, kind=kind, amount_cents=amount_cents,
        period_days=period_days, score_min=score_min,
        event_type=event_type, max_count=max_count, active=active,
    )
    await _audit(account_id, user_id, "driver_pay_rule_created", str(rule_id))
    return rule_id


async def update_rule(
    account_id: int, rule_id: int, *, user_id: int, **fields,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    ok = await tenant.update_bonus_rule(account_id, rule_id, **fields)
    if ok:
        await _audit(account_id, user_id, "driver_pay_rule_updated", str(rule_id))
    return ok


async def delete_rule(
    account_id: int, rule_id: int, *, user_id: int,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    await tenant.delete_bonus_rule(account_id, rule_id)
    await _audit(account_id, user_id, "driver_pay_rule_deleted", str(rule_id))
    return True


# ── Driver settings ────────────────────────────────────────────────

async def list_driver_settings(account_id: int) -> list[dict]:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.list_driver_pay_settings(account_id)


async def upsert_driver_settings(
    account_id: int, driver_id: str, *, user_id: int,
    base_pay_cents: int = 0, opt_in: bool = True,
    pay_model: str = "", pay_rate: float | None = None,
) -> None:
    from .engine import VALID_PAY_MODELS
    if (pay_model or "").strip().lower() not in VALID_PAY_MODELS:
        raise ValueError(f"pay_model must be one of {VALID_PAY_MODELS}")
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    await tenant.upsert_driver_pay_settings(
        account_id, driver_id,
        base_pay_cents=base_pay_cents, opt_in=opt_in,
        pay_model=pay_model, pay_rate=pay_rate,
    )
    await _audit(
        account_id, user_id, "driver_pay_driver_settings_updated", driver_id,
    )


# ── Run lifecycle ──────────────────────────────────────────────────

async def create_run(
    account_id: int, *, user_id: int,
    period_start: date, period_end: date,
) -> int:
    """Compute driver pay for the given period and persist as a draft run.

    Returns the new run_id.  Status starts as ``draft``; admins finalize
    it via :func:`finalize_run` once they've reviewed.
    """
    await _assert_enabled(account_id)
    if period_end < period_start:
        raise ValueError("period_end must be >= period_start")

    tenant = await get_tenant_db(account_id)
    run_id = await tenant.create_driver_pay_run(
        account_id,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        created_by=user_id,
    )
    await _fill_run_items(account_id, tenant, run_id, period_start, period_end)
    await _audit(account_id, user_id, "driver_pay_run_created", str(run_id))
    return run_id


async def _fill_run_items(
    account_id: int, tenant, run_id: int,
    period_start: date, period_end: date,
) -> None:
    """(Re)compute the period and persist one item per driver + the run's
    NET grand total.  Shared by create and draft-recompute."""
    items: list[RunItem] = await compute_run(account_id, period_start, period_end)
    grand_total = 0
    for item in items:
        await tenant.add_driver_pay_run_item(
            run_id=run_id,
            driver_id=item.driver_id,
            driver_name=item.driver_name,
            base_pay_cents=item.base_pay_cents,
            bonus_total_cents=item.bonus_total_cents,
            total_cents=item.total_cents,
            breakdown=[b.to_dict() for b in item.breakdown],
            deductions_cents=item.deductions_cents,
            net_cents=item.net_cents,
            # Frozen itemized snapshot — the settlement statement's detail.
            statement={
                "driver_user_id": item.driver_user_id,
                "zero_pay_loads": item.zero_pay_loads,
                "loads": item.load_lines,
                "additions": item.addition_lines,
                "deductions": item.deduction_lines,
                "base_pay_cents": item.base_pay_cents,
                "load_earnings_cents": item.load_earnings_cents,
                "extras_cents": item.extras_cents,
                "bonus_total_cents": item.bonus_total_cents,
                "total_cents": item.total_cents,
                "deductions_cents": item.deductions_cents,
                "net_cents": item.net_cents,
            },
        )
        # The run's grand total is NET — what's actually disbursed.
        grand_total += item.net_cents
    await tenant.set_driver_pay_run_total(account_id, run_id, grand_total)


async def recompute_run(
    account_id: int, run_id: int, *, user_id: int,
) -> bool:
    """Re-pull the period into an existing DRAFT run — picks up line items
    (additions/deductions) added since it was created, so accounting can
    fix a forgotten entry on the load and refresh the statement in place.
    Refuses a finalized run (a settled statement never silently changes)."""
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    run = await tenant.get_driver_pay_run(account_id, run_id)
    if run is None:
        return False
    if run["status"] != STATUS_DRAFT:
        raise ValueError("only a draft run can be recomputed")
    from datetime import date as _date
    ps = _date.fromisoformat(run["period_start"])
    pe = _date.fromisoformat(run["period_end"])
    await tenant.delete_driver_pay_run_items(run_id)
    await _fill_run_items(account_id, tenant, run_id, ps, pe)
    await _audit(account_id, user_id, "driver_pay_run_recomputed", str(run_id))
    return True


async def list_runs(account_id: int, *, limit: int = 50) -> list[dict]:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.list_driver_pay_runs(account_id, limit=limit)


async def get_run_detail(account_id: int, run_id: int) -> Optional[dict]:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return None
    run = await tenant.get_driver_pay_run(account_id, run_id)
    if run is None:
        return None
    items = await tenant.get_driver_pay_run_items(run_id)
    run["items"] = items
    return run


async def finalize_run(
    account_id: int, run_id: int, *, user_id: int,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    run = await tenant.get_driver_pay_run(account_id, run_id)
    if run is None:
        return False
    if run["status"] != STATUS_DRAFT:
        raise ValueError(
            f"can only finalize a draft run (current status: {run['status']})"
        )
    await tenant.set_driver_pay_run_status(account_id, run_id, STATUS_FINALIZED)
    await _audit(account_id, user_id, "driver_pay_run_finalized", str(run_id))
    return True


async def cancel_run(
    account_id: int, run_id: int, *, user_id: int,
) -> bool:
    await _assert_enabled(account_id)
    tenant = await get_tenant_db(account_id)
    run = await tenant.get_driver_pay_run(account_id, run_id)
    if run is None:
        return False
    if run["status"] not in (STATUS_DRAFT, STATUS_FINALIZED):
        return False
    await tenant.set_driver_pay_run_status(account_id, run_id, STATUS_CANCELLED)
    await _audit(account_id, user_id, "driver_pay_run_cancelled", str(run_id))
    return True


async def set_run_status(
    account_id: int, run_id: int, status: str, *, user_id: int,
) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    if status == STATUS_FINALIZED:
        return await finalize_run(account_id, run_id, user_id=user_id)
    if status == STATUS_CANCELLED:
        return await cancel_run(account_id, run_id, user_id=user_id)
    # Reverting a cancelled or finalized run back to draft is not allowed.
    raise ValueError(f"cannot transition to {status!r} via API")


# ── Driver self-service ────────────────────────────────────────────

async def get_paystub_history(
    account_id: int, driver_id: str, *, limit: int = 24,
) -> list[dict]:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return []
    return await tenant.get_paystub_history_for_driver(
        account_id, driver_id, limit=limit,
    )

