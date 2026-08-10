"""Incentive runs — where the configuration becomes money.

A run is one period for one account: rows generated from the canonical
loads, hand-adjusted where the data cannot know (inactive days, extras),
exception-overridden with reasons, then finalized.  The run grid mirrors
the customer's settlement sheet column-for-column so the output is a
document they already know how to read.

THE SNAPSHOT IS THE CONTRACT.  ``create_run`` freezes the account's
config — model, tiers, targets — into the run row, billing-snapshot
style.  Every later recompute inside the run reads the SNAPSHOT, never
the live config: rules change ("Effective for June 2026"), and an old
period must keep paying under the rules it was announced with.

WHAT IS DERIVED AND WHAT IS MANUAL, and why:
  * base_gross, miles, the (dispatcher × company × truck) grouping and
    the assignment window all derive from LOADS — the same canonical
    rows the grades read.  Cancelled loads don't count.
  * The window derives from first/last load date clamped to the period
    (the sheet's trucks joined mid-period: 11/10–11/30), and stays
    EDITABLE — the data shows when a truck worked, not when it was
    assigned.
  * extras are MANUAL, matching the sheet's hand-typed column: off-load
    line items carry no vehicle, so auto-attributing a layover to a
    truck would be a guess wearing a number.
  * inactive_days are MANUAL now, by owner decision, with the source
    interface arriving later from the cross-role dashboard (repair →
    inactive, home time → inactive).

A truck whose company has NO target resolves to 0% with the target left
NULL — visible and explicable on the row, never a silently-invented bar.
"""

from __future__ import annotations

import json
from datetime import date

from features.kpi.dispatch import engine
from features.loads import service as loads_service
from infra.platform import get_tenant_db


class RunError(Exception):
    """User-facing run failures (409/422 material, not bugs)."""


def _days_inclusive(start: str, end: str) -> int:
    try:
        d0 = date.fromisoformat(start[:10])
        d1 = date.fromisoformat(end[:10])
    except ValueError:
        return 0
    return max(0, (d1 - d0).days + 1)


def _recompute(row: dict, snapshot: dict) -> dict:
    """Row inputs → engine outputs, ALWAYS from the run's snapshot."""
    active_days = max(0, int(row["total_days"]) - int(row["inactive_days"]))
    target = row.get("weekly_target")
    if target is None:
        # No bar for this company: nothing to measure against, so the
        # row pays 0 and says why (weekly_target stays NULL in the grid).
        computed = {
            "kpi_gross": engine.money(
                float(row["base_gross"]) + float(row["extras"])),
            "rpm": engine.compute_rpm(float(row["base_gross"]),
                                      float(row["miles"])),
            "adjusted_target": 0.0,
            "pct": 0.0,
            "kpi_dollars": 0.0,
        }
    else:
        out = engine.compute_truck_row(
            snapshot["config"], snapshot["tiers"],
            base_gross=float(row["base_gross"]),
            extras=float(row["extras"]),
            miles=float(row["miles"]),
            weekly_target=float(target),
            active_days=active_days,
        )
        computed = {k: out[k] for k in (
            "kpi_gross", "rpm", "adjusted_target", "pct", "kpi_dollars")}
    # An override survives recompute — it is a human decision about the
    # ROW, not about one intermediate value — but its dollars re-derive
    # from the fresh gross.
    if row.get("override_pct") is not None:
        computed["confirmed_dollars"] = engine.money(
            computed["kpi_gross"] * float(row["override_pct"]) / 100)
    else:
        computed["confirmed_dollars"] = computed["kpi_dollars"]
    return computed


async def create_run(
    account_id: int, *, period_start: str, period_end: str, created_by: int,
) -> int:
    """Generate a draft run: snapshot the config, group the period's
    loads by (dispatcher, company, truck), compute every row."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")

    config = await tenant.get_kpi_incentive_config(account_id)
    if config is None or not config.get("tiers"):
        raise RunError(
            "Incentives are not configured — set the model, tiers and "
            "company targets in KPI configuration first."
        )
    targets = await tenant.list_kpi_company_targets(account_id)
    snapshot = {
        "config": {k: config[k] for k in (
            "model", "combine_rule", "calc_cadence", "calc_custom_days",
            "exception_cap_pct", "floor_weekly_gross", "floor_rpm")},
        "tiers": config["tiers"],
        "targets": {t["company_code"]: t["weekly_gross_target"]
                    for t in targets},
    }

    loads = await loads_service.get_loads(
        account_id, since=period_start, until=period_end, limit=None,
    )

    groups: dict[tuple, dict] = {}
    for l in loads:
        if l.get("status") == "canceled":
            continue
        key = (
            l.get("dispatcher_user_id") or f"name:{l.get('dispatcher_name')}",
            l.get("company_code") or "",
            l.get("vehicle_unit") or "",
        )
        g = groups.setdefault(key, {
            "dispatcher_user_id": l.get("dispatcher_user_id"),
            "dispatcher_name": (l.get("dispatcher_name") or "").strip()
                               or "(unassigned)",
            "company_code": l.get("company_code") or "",
            "vehicle_unit": l.get("vehicle_unit") or "",
            "base_gross": 0.0, "miles": 0.0,
            "_dates": [],
        })
        g["base_gross"] += float(l.get("total_rate") or 0)
        g["miles"] += float(l.get("loaded_miles") or 0)
        g["miles"] += float(l.get("empty_miles") or 0)
        for d in (l.get("pickup_date"), l.get("delivery_date")):
            if d:
                g["_dates"].append(str(d)[:10])

    run_id = await tenant.create_kpi_run(
        account_id, period_start=period_start, period_end=period_end,
        config_snapshot=json.dumps(snapshot), created_by=created_by,
    )

    for g in groups.values():
        dates = sorted(g.pop("_dates"))
        w_start = max(period_start[:10], dates[0]) if dates else period_start[:10]
        w_end = min(period_end[:10], dates[-1]) if dates else period_end[:10]
        row = {
            **g,
            "window_start": w_start,
            "window_end": w_end,
            "total_days": _days_inclusive(w_start, w_end),
            "inactive_days": 0,
            "inactive_reason": "",
            "extras": 0.0,
            "extras_note": "",
            "weekly_target": snapshot["targets"].get(g["company_code"]),
            "override_pct": None,
            "override_reason": "",
        }
        row.update(_recompute(row, snapshot))
        await tenant.insert_kpi_run_row(run_id, account_id, row)

    return run_id


async def _open_run(tenant, account_id: int, run_id: int) -> dict:
    run = await tenant.get_kpi_run(account_id, run_id)
    if run is None:
        raise RunError("run not found")
    if run["status"] != "draft":
        raise RunError(
            "this run is finalized — finalized periods are the paid "
            "record and are never re-priced"
        )
    return run


async def update_row(
    account_id: int, run_id: int, row_id: int, fields: dict,
    *, updated_by: int,
) -> dict:
    """Edit a row's INPUTS (window, inactive days, extras) and recompute
    its outputs from the run's snapshot."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    run = await _open_run(tenant, account_id, run_id)
    row = await tenant.get_kpi_run_row(account_id, run_id, row_id)
    if row is None:
        raise RunError("row not found")

    editable = {"window_start", "window_end", "inactive_days",
                "inactive_reason", "extras", "extras_note"}
    patch = {k: v for k, v in fields.items() if k in editable and v is not None}
    row.update(patch)
    if {"window_start", "window_end"} & patch.keys():
        row["total_days"] = _days_inclusive(
            row["window_start"], row["window_end"])
    if int(row["inactive_days"]) < 0:
        raise RunError("inactive_days cannot be negative")
    if int(row["inactive_days"]) > int(row["total_days"]):
        raise RunError(
            f"inactive_days ({row['inactive_days']}) exceeds the window's "
            f"{row['total_days']} days")

    snapshot = json.loads(run["config_snapshot"])
    row.update(_recompute(row, snapshot))
    await tenant.update_kpi_run_row(account_id, run_id, row_id, {
        **patch, "total_days": row["total_days"],
        "kpi_gross": row["kpi_gross"], "rpm": row["rpm"],
        "adjusted_target": row["adjusted_target"], "pct": row["pct"],
        "kpi_dollars": row["kpi_dollars"],
        "confirmed_dollars": row["confirmed_dollars"],
    })
    return await tenant.get_kpi_run_row(account_id, run_id, row_id)


async def set_exception(
    account_id: int, run_id: int, row_id: int,
    *, override_pct: float | None, reason: str, confirmed_by: int,
) -> dict:
    """Apply or clear a manual override — the sheet's "Confirmed KPI-$"
    with its Reason column.  Validated against the SNAPSHOT's cap, and a
    reason is mandatory: an unexplained override is a payout nobody can
    audit."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    run = await _open_run(tenant, account_id, run_id)
    row = await tenant.get_kpi_run_row(account_id, run_id, row_id)
    if row is None:
        raise RunError("row not found")
    snapshot = json.loads(run["config_snapshot"])

    if override_pct is None:
        confirmed = float(row["kpi_dollars"])
        patch = {"override_pct": None, "override_reason": "",
                 "confirmed_dollars": confirmed,
                 "confirmed_by": confirmed_by,
                 "confirmed_at": ""}
    else:
        if not reason.strip():
            raise RunError("an exception needs a reason")
        try:
            engine.validate_exception(override_pct, snapshot["config"])
        except ValueError as e:
            raise RunError(str(e))
        confirmed = engine.money(
            float(row["kpi_gross"]) * float(override_pct) / 100)
        patch = {"override_pct": float(override_pct),
                 "override_reason": reason.strip(),
                 "confirmed_dollars": confirmed,
                 "confirmed_by": confirmed_by,
                 "confirmed_at": ""}
    await tenant.update_kpi_run_row(account_id, run_id, row_id, patch)
    return await tenant.get_kpi_run_row(account_id, run_id, row_id)


async def get_run_detail(account_id: int, run_id: int) -> dict:
    """The run + its rows + per-dispatcher payout totals — the sheet."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    run = await tenant.get_kpi_run(account_id, run_id)
    if run is None:
        raise RunError("run not found")
    rows = await tenant.list_kpi_run_rows(account_id, run_id)
    payouts: dict[str, float] = {}
    for r in rows:
        key = r["dispatcher_name"]
        payouts[key] = engine.money(
            payouts.get(key, 0.0) + float(r["confirmed_dollars"]))
    run.pop("config_snapshot", None)
    return {**run, "rows": rows, "payouts": payouts}


async def finalize_run(account_id: int, run_id: int,
                       *, finalized_by: int) -> dict:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    await _open_run(tenant, account_id, run_id)
    await tenant.finalize_kpi_run(account_id, run_id, finalized_by)
    return await get_run_detail(account_id, run_id)
