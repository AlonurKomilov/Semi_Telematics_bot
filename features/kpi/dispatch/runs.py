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

import csv
import io
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from features.kpi.dispatch import engine
from features.loads import service as loads_service
from infra.platform import get_tenant_db


logger = logging.getLogger(__name__)

_one_day = timedelta(days=1)


class RunError(Exception):
    """User-facing run failures (409/422 material, not bugs)."""


def _days_inclusive(start: str, end: str) -> int:
    try:
        d0 = date.fromisoformat(start[:10])
        d1 = date.fromisoformat(end[:10])
    except ValueError:
        return 0
    return max(0, (d1 - d0).days + 1)


def _clean_inactive_dates(raw, window_start: str, window_end: str) -> list[dict]:
    """Validate the board's day marks: ISO dates inside the window, no
    duplicates, reasons trimmed.  Raises RunError in admin language."""
    if not isinstance(raw, list):
        raise RunError("inactive_dates must be a list of {date, reason}")
    marks: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or not item.get("date"):
            raise RunError("each inactive date needs a 'date'")
        d = str(item["date"])[:10]
        try:
            date.fromisoformat(d)
        except ValueError:
            raise RunError(f"'{d}' is not a date")
        if not (window_start[:10] <= d <= window_end[:10]):
            raise RunError(
                f"{d} is outside the row's window "
                f"({window_start[:10]} – {window_end[:10]})")
        if d in seen:
            continue
        seen.add(d)
        marks.append({"date": d, "reason": str(item.get("reason") or "").strip()})
    marks.sort(key=lambda m: m["date"])
    return marks


def _parse_inactive_dates(row: dict) -> dict:
    """Storage keeps the day list as JSON text; the wire carries a list."""
    raw = row.get("inactive_dates")
    if isinstance(raw, str):
        try:
            row["inactive_dates"] = json.loads(raw or "[]")
        except ValueError:
            row["inactive_dates"] = []
    elif raw is None:
        row["inactive_dates"] = []
    return row


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
            "zero_reason": "no_target",
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
            "kpi_gross", "rpm", "adjusted_target", "pct", "kpi_dollars",
            "zero_reason")}
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

    # Validate the period BEFORE the run row exists — a 422 must never
    # leave an orphan empty run behind.
    try:
        date.fromisoformat(period_start[:10])
        date.fromisoformat(period_end[:10])
    except ValueError:
        raise RunError("period dates are not valid dates")

    run_id = await tenant.create_kpi_run(
        account_id, period_start=period_start, period_end=period_end,
        config_snapshot=json.dumps(snapshot), created_by=created_by,
    )

    for g in groups.values():
        g.pop("_dates")
        # The window is the WHOLE period, and every day in it COUNTS
        # unless a HUMAN marks it inactive (repair, home time,
        # holiday).  A day without loads is "empty" — filling it is
        # the dispatcher's job, so it weighs against them; an earlier
        # auto-excuse of edge days hid dispatcher idle time behind a
        # shrunken target.  Owner rule 2026-08-19.
        w_start = period_start[:10]
        w_end = period_end[:10]
        row = {
            **g,
            "window_start": w_start,
            "window_end": w_end,
            "total_days": _days_inclusive(w_start, w_end),
            "inactive_days": 0,
            "inactive_reason": "",
            "inactive_dates": "[]",
            "extras": 0.0,
            "extras_note": "",
            "weekly_target": snapshot["targets"].get(g["company_code"]),
            "override_pct": None,
            "override_reason": "",
        }
        row.update(_recompute(row, snapshot))
        await tenant.insert_kpi_run_row(run_id, account_id, row)

    from capabilities.activity_trail import record_simple
    # created_by=0 is the auto-run scheduler — the trail is people-only,
    # so a system creation records with no actor (renders as "system").
    await record_simple(
        tenant, account_id, created_by or None, "create", "kpi_run", run_id,
        changes={"period": {"from": None,
                            "to": f"{period_start[:10]} – {period_end[:10]}"},
                 "rows": {"from": None, "to": len(groups)}},
        context=None if created_by else {"system": "kpi auto-run scheduler"},
    )
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
    before = dict(_parse_inactive_dates(dict(row)))

    editable = {"window_start", "window_end", "inactive_days",
                "inactive_reason", "inactive_dates", "extras", "extras_note"}
    patch = {k: v for k, v in fields.items() if k in editable and v is not None}
    row.update({k: v for k, v in patch.items() if k != "inactive_dates"})
    if {"window_start", "window_end"} & patch.keys():
        row["total_days"] = _days_inclusive(
            row["window_start"], row["window_end"])

    if "inactive_dates" in patch:
        # The board's per-day marking: [{date, reason}, …].  The count
        # DERIVES from the list; a summary reason derives from the
        # distinct day reasons (unless an explicit one was also sent).
        marks = _clean_inactive_dates(
            patch["inactive_dates"], row["window_start"], row["window_end"])
        row["inactive_dates"] = json.dumps(marks)
        row["inactive_days"] = len(marks)
        if "inactive_reason" not in patch:
            seen: list[str] = []
            for m in marks:
                r = m["reason"]
                if r and r not in seen:
                    seen.append(r)
            row["inactive_reason"] = ", ".join(seen)
        patch = {**patch, "inactive_dates": row["inactive_dates"],
                 "inactive_days": row["inactive_days"],
                 "inactive_reason": row["inactive_reason"]}
    elif "inactive_days" in patch:
        # The typed number is the coarse tool — it wins by clearing the
        # day list, but ONLY when it actually disagrees with it.  The
        # dialog echoes the current count on every save (an extras-only
        # edit included), and an echo must not destroy the per-day
        # marks (the auto-excused days ride that list).
        try:
            existing = json.loads(row.get("inactive_dates") or "[]")
        except ValueError:
            existing = []
        if int(patch["inactive_days"]) != len(existing):
            row["inactive_dates"] = "[]"
            patch = {**patch, "inactive_dates": "[]"}

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
        # WHO adjusted the payroll input, WHEN — the audit's governance
        # gap: the actor arrived here and was discarded.
        "adjusted_by": updated_by,
        "adjusted_at": datetime.now(timezone.utc).isoformat(),
        "kpi_gross": row["kpi_gross"], "rpm": row["rpm"],
        "adjusted_target": row["adjusted_target"], "pct": row["pct"],
        "kpi_dollars": row["kpi_dollars"],
        "zero_reason": row.get("zero_reason", ""),
        "confirmed_dollars": row["confirmed_dollars"],
    })
    fresh = _parse_inactive_dates(
        await tenant.get_kpi_run_row(account_id, run_id, row_id))
    from capabilities.activity_trail import diff_rows, record_simple
    changes = diff_rows(before, fresh, fields=[
        "window_start", "window_end", "inactive_days", "inactive_reason",
        "inactive_dates", "extras", "extras_note",
        "adjusted_target", "pct", "confirmed_dollars",
    ])
    if changes:
        await record_simple(
            tenant, account_id, updated_by, "row_update", "kpi_run", run_id,
            changes=changes,
            context={"row_id": row_id,
                     "unit": row.get("vehicle_unit") or "",
                     "dispatcher": row.get("dispatcher_name") or ""},
        )
    return fresh


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
    before = dict(row)
    snapshot = json.loads(run["config_snapshot"])

    if override_pct is None:
        confirmed = float(row["kpi_dollars"])
        patch = {"override_pct": None, "override_reason": "",
                 "confirmed_dollars": confirmed,
                 "confirmed_by": confirmed_by,
                 "confirmed_at": datetime.now(timezone.utc).isoformat()}
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
                 "confirmed_at": datetime.now(timezone.utc).isoformat()}
    await tenant.update_kpi_run_row(account_id, run_id, row_id, patch)
    fresh = _parse_inactive_dates(
        await tenant.get_kpi_run_row(account_id, run_id, row_id))
    from capabilities.activity_trail import diff_rows, record_simple
    changes = diff_rows(before, fresh, fields=[
        "override_pct", "override_reason", "confirmed_dollars",
    ])
    if changes:
        await record_simple(
            tenant, account_id, confirmed_by, "row_override", "kpi_run",
            run_id, changes=changes,
            context={"row_id": row_id,
                     "unit": row.get("vehicle_unit") or "",
                     "dispatcher": row.get("dispatcher_name") or ""},
        )
    return fresh


async def get_run_detail(account_id: int, run_id: int) -> dict:
    """The run + its rows + per-dispatcher payout totals — the sheet."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    run = await tenant.get_kpi_run(account_id, run_id)
    if run is None:
        raise RunError("run not found")
    rows = [_parse_inactive_dates(r)
            for r in await tenant.list_kpi_run_rows(account_id, run_id)]
    payouts: dict[str, float] = {}
    for r in rows:
        key = r["dispatcher_name"]
        payouts[key] = engine.money(
            payouts.get(key, 0.0) + float(r["confirmed_dollars"]))

    # DRAFT rows carry their next-tier gap — "how far to the next
    # percent", ephemeral (recomputed per read, never stored): a
    # finalized row has no next move, so it carries none.
    if run["status"] == "draft":
        try:
            snap = json.loads(run.get("config_snapshot") or "{}")
            for r in rows:
                active = max(0, int(r["total_days"]) - int(r["inactive_days"]))
                r["next_tier"] = engine.next_tier_gap(
                    snap.get("config", {}), snap.get("tiers", []),
                    base_gross=float(r["base_gross"]),
                    extras=float(r["extras"]),
                    miles=float(r["miles"]),
                    weekly_target=r.get("weekly_target"),
                    active_days=active,
                    current_pct=float(r["pct"]),
                )
        except ValueError:
            pass
    # EVERY paid row carries the rule that priced it — the zero rows
    # explain themselves via zero_reason; this is the same courtesy for
    # the paid ones.  Ephemeral, recomputed from the frozen snapshot
    # (drafts AND finalized — the snapshot never re-prices, so the
    # recompute is deterministic).  Guard: attach only when the
    # recomputed percent equals the stored one, so an engine change can
    # never explain an old run with today's logic.  Overrides are the
    # human's rule, told by override_reason.
    try:
        snap = json.loads(run.get("config_snapshot") or "{}")
        for r in rows:
            if r.get("override_pct") is not None or float(r["pct"]) <= 0:
                continue
            active = max(0, int(r["total_days"]) - int(r["inactive_days"]))
            if active <= 0:
                continue
            kpi_gross = engine.money(
                float(r["base_gross"]) + float(r["extras"]))
            weekly_eq = engine.weekly_equivalent(kpi_gross, active)
            rpm = engine.compute_rpm(float(r["base_gross"]), float(r["miles"]))
            wt = r.get("weekly_target")
            target_met = wt is not None and kpi_gross >= engine.adjusted_target(
                float(wt), active)
            rule = engine.matched_rule(
                snap.get("config", {}), snap.get("tiers", []),
                weekly_eq=weekly_eq, rpm=rpm, target_met=target_met)
            if rule is not None and float(rule["pct"]) == float(r["pct"]):
                r["matched_rule"] = rule
    except ValueError:
        pass
    # Names for the attribution fields (adjusted_by / confirmed_by /
    # created_by) — ids alone explain nothing in a drawer.
    user_names: dict[str, str] = {}
    try:
        for u in await tenant.list_account_users(account_id):
            user_names[str(u.id)] = (u.display_name or "").strip() or f"user {u.id}"
    except Exception:
        pass
    # The UI explains zeros with the thresholds that CAUSED them, so the
    # snapshot's policy knobs ride along (floors + cap — never the whole
    # snapshot; tiers stay server-side).
    try:
        snap_cfg = json.loads(run.get("config_snapshot") or "{}").get(
            "config", {})
    except ValueError:
        snap_cfg = {}
    run.pop("config_snapshot", None)
    return {
        **run, "rows": rows, "payouts": payouts,
        "user_names": user_names,
        "snapshot_config": {
            "floor_weekly_gross": snap_cfg.get("floor_weekly_gross"),
            "floor_rpm": snap_cfg.get("floor_rpm"),
            "exception_cap_pct": snap_cfg.get("exception_cap_pct"),
        },
    }


def _group_key(l: dict) -> tuple:
    """The row identity a load belongs to — the SAME key create_run
    groups by, factored out so the board's load listing can never drift
    from the generator's grouping."""
    return (
        l.get("dispatcher_user_id") or f"name:{l.get('dispatcher_name')}",
        l.get("company_code") or "",
        l.get("vehicle_unit") or "",
    )


async def get_run_loads(account_id: int, run_id: int) -> dict:
    """The individual loads behind every settlement row — the board view.

    Loads are LIVE data while the run is a SNAPSHOT, so each row also
    reports drift: True when the loads that group to it today no longer
    sum to the gross/miles the row was generated from (a load edited or
    added after generation).  The UI surfaces that instead of letting
    the board silently disagree with the sheet.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    run = await tenant.get_kpi_run(account_id, run_id)
    if run is None:
        raise RunError("run not found")
    rows = [_parse_inactive_dates(r)
            for r in await tenant.list_kpi_run_rows(account_id, run_id)]

    loads = await loads_service.get_loads(
        account_id, since=run["period_start"], until=run["period_end"],
        limit=None,
    )
    by_key: dict[tuple, list[dict]] = {}
    for l in loads:
        if l.get("status") == "canceled":
            continue
        by_key.setdefault(_group_key(l), []).append(l)

    out_rows: dict[int, list[dict]] = {}
    drift: list[int] = []
    for row in rows:
        key = (
            row.get("dispatcher_user_id") or f"name:{row.get('dispatcher_name')}",
            row.get("company_code") or "",
            row.get("vehicle_unit") or "",
        )
        row_loads = by_key.pop(key, [])
        out_rows[int(row["id"])] = [{
            "load_number": l.get("load_number") or "",
            "status": l.get("status") or "",
            "pickup_date": str(l.get("pickup_date") or "")[:10],
            "delivery_date": str(l.get("delivery_date") or "")[:10],
            "pickup_location": l.get("pickup_location") or "",
            "delivery_location": l.get("delivery_location") or "",
            "total_rate": float(l.get("total_rate") or 0),
            "miles": float(l.get("loaded_miles") or 0)
                     + float(l.get("empty_miles") or 0),
        } for l in row_loads]
        gross_now = sum(float(l.get("total_rate") or 0) for l in row_loads)
        miles_now = sum(float(l.get("loaded_miles") or 0)
                        + float(l.get("empty_miles") or 0) for l in row_loads)
        if (abs(gross_now - float(row["base_gross"])) > 0.01
                or abs(miles_now - float(row["miles"])) > 0.01):
            drift.append(int(row["id"]))

    # Loads whose group has NO row: added after generation.  Their
    # existence is itself drift the manager should see.
    unmatched = sum(len(v) for v in by_key.values())

    # Phase 4b stepping stone: maintenance work orders SUGGEST inactive
    # days — a truck in the shop on its service date is plausibly not
    # earning.  Suggestions only: the manager confirms with a click
    # (owner decision: inactive days stay human-confirmed until the
    # cross-role dashboard).  Days already marked are not re-suggested.
    suggestions: dict[int, list[dict]] = {}
    try:
        wos = await tenant.list_work_orders(account_id)
    except Exception:
        wos = []
    if wos:
        for row in rows:
            marked = {m.get("date") for m in (row.get("inactive_dates") or [])}
            # Days the row's loads COVER (pickup through delivery) —
            # suggesting to excuse a day that earned money proposes
            # zeroing revenue out of the count; a shop visit on a
            # working day didn't stop the work.
            covered: set[str] = set()
            for l in out_rows.get(int(row["id"]), []):
                a = l["pickup_date"]
                if not a:
                    continue
                b = l["delivery_date"] or a
                cur = date.fromisoformat(a)
                stop = date.fromisoformat(b if b >= a else a)
                while cur <= stop:
                    covered.add(cur.isoformat())
                    cur += _one_day
            hits = []
            for wo in wos:
                day = str(wo.get("service_date") or "")[:10]
                if not day or day in marked or day in covered:
                    continue
                if wo.get("vehicle_name") != row.get("vehicle_unit"):
                    continue
                # A WO without a company code still matches its unit —
                # units are reused across companies, so a code, when
                # present, must agree.
                if (wo.get("company_code")
                        and wo["company_code"] != row.get("company_code")):
                    continue
                if not (row["window_start"][:10] <= day
                        <= row["window_end"][:10]):
                    continue
                hits.append({
                    "date": day,
                    "reason": "repair",
                    "source": (f"WO #{wo.get('id')}"
                               + (f" · {wo['vendor_name']}"
                                  if wo.get("vendor_name") else "")),
                })
            if hits:
                suggestions[int(row["id"])] = sorted(
                    hits, key=lambda h: h["date"])

    # The retired A–D grades view, folded in where it still earns its
    # keep: each dispatcher's grade for THIS PERIOD, computed from the
    # same loads by the same grading engine against the live thresholds
    # (grades are analytics — they never touch the payout math).
    dispatcher_grades: dict[str, str] = {}
    try:
        from features.kpi.dispatch.grades import compute_dispatcher_kpis
        from features.kpi.dispatch.thresholds import get_kpi_thresholds

        th = await get_kpi_thresholds(tenant, account_id)
        for m in compute_dispatcher_kpis(loads, th):
            dispatcher_grades[m["dispatcher_name"]] = m.get("grade", "")
    except Exception:
        logger.exception("run grades failed account=%s run=%s",
                         account_id, run_id)

    return {"rows": out_rows, "drift": drift, "unmatched_loads": unmatched,
            "suggestions": suggestions,
            "dispatcher_grades": dispatcher_grades}


async def finalize_run(account_id: int, run_id: int,
                       *, finalized_by: int) -> dict:
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    await _open_run(tenant, account_id, run_id)
    await tenant.finalize_kpi_run(account_id, run_id, finalized_by)
    detail = await get_run_detail(account_id, run_id)
    from capabilities.activity_trail import record_simple
    total = round(sum(
        float(r["confirmed_dollars"]) for r in detail["rows"]), 2)
    await record_simple(
        tenant, account_id, finalized_by, "finalize", "kpi_run", run_id,
        changes={"status": {"from": "draft", "to": "finalized"},
                 "total": {"from": None, "to": total},
                 "rows": {"from": None, "to": len(detail["rows"])}},
    )
    return detail


async def export_run_csv(account_id: int, run_id: int) -> tuple[str, str]:
    """The settlement as a file — the sheet payroll actually receives.

    Column set mirrors the run grid (which mirrors the customer's
    Excel), one row per truck, then per-dispatcher totals.  A DRAFT
    exports too (managers circulate drafts for review) but says so in
    the filename and the header line.
    """
    detail = await get_run_detail(account_id, run_id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Incentive run",
                f"{detail['period_start']} - {detail['period_end']}",
                detail["status"]])
    w.writerow([])
    w.writerow(["Dispatcher", "Company", "Unit", "Window start",
                "Window end", "Days", "Inactive days", "Inactive reason",
                "Gross", "Extras", "Extras note", "Miles", "RPM",
                "Weekly target", "Adjusted target", "KPI %", "KPI $",
                "Override %", "Override reason", "Confirmed $",
                "Zero reason"])
    for r in detail["rows"]:
        w.writerow([
            r["dispatcher_name"], r["company_code"], r["vehicle_unit"],
            r["window_start"], r["window_end"], r["total_days"],
            r["inactive_days"], r["inactive_reason"], r["kpi_gross"],
            r["extras"], r["extras_note"], r["miles"], r["rpm"],
            r["weekly_target"], r["adjusted_target"], r["pct"],
            r["kpi_dollars"], r["override_pct"], r["override_reason"],
            r["confirmed_dollars"], r["zero_reason"],
        ])
    w.writerow([])
    w.writerow(["Dispatcher totals"])
    for name, total in detail["payouts"].items():
        w.writerow([name, total])
    w.writerow(["TOTAL", engine.payout_total(list(detail["payouts"].values()))])
    filename = (f"incentive-run-{detail['period_start']}"
                f"-{detail['period_end']}-{detail['status']}.csv")
    return filename, buf.getvalue()


async def monthly_payouts(account_id: int, month: str) -> dict:
    """Per-dispatcher totals across the FINALIZED runs of one month —
    the weekly-calc → monthly-payout roll-up (owner decision: cadence
    is weekly by default, but some dispatchers are paid monthly).

    A run belongs to the month its PERIOD ENDS in, so a week straddling
    a month boundary is counted exactly once, in the month the sheet
    would have been settled.  Drafts are excluded — a payout total that
    could still change is not a payout total.
    """
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise RunError("month must look like 2026-08")
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    runs = [r for r in await tenant.list_kpi_runs(account_id)
            if r["status"] == "finalized" and r["period_end"][:7] == month]
    payouts: dict[str, float] = {}
    for run in runs:
        for row in await tenant.list_kpi_run_rows(account_id, run["id"]):
            name = row["dispatcher_name"]
            payouts[name] = engine.money(
                payouts.get(name, 0.0) + float(row["confirmed_dollars"]))
    return {
        "month": month,
        "runs": [{k: r[k] for k in ("id", "period_start", "period_end")}
                 for r in runs],
        "payouts": payouts,
        "total": engine.payout_total(list(payouts.values())),
    }


async def my_payouts(account_id: int, user_id: int,
                     *, limit_runs: int = 26) -> dict:
    """A dispatcher's OWN payouts — finalized runs only, their rows
    only.  Drafts never appear: showing someone unconfirmed money and
    then paying less is how trust dies.  Matching is by
    ``dispatcher_user_id`` (the team-integration link), so an unlinked
    TMS dispatcher name yields an empty page rather than a guess."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    finalized = [r for r in await tenant.list_kpi_runs(account_id)
                 if r["status"] == "finalized"][:limit_runs]
    out = []
    for run in finalized:
        rows = [
            {k: r[k] for k in (
                "company_code", "vehicle_unit", "window_start",
                "window_end", "total_days", "inactive_days", "kpi_gross",
                "miles", "rpm", "pct", "confirmed_dollars", "zero_reason")}
            for r in await tenant.list_kpi_run_rows(account_id, run["id"])
            if r.get("dispatcher_user_id") == user_id
        ]
        if rows:
            out.append({
                "run_id": run["id"],
                "period_start": run["period_start"],
                "period_end": run["period_end"],
                "rows": rows,
                "total": engine.payout_total(
                    [float(r["confirmed_dollars"]) for r in rows]),
            })
    return {"runs": out}


async def preview_rules(account_id: int, config: dict,
                        tiers: list[dict]) -> dict:
    """Re-price the LATEST run's rows under a CANDIDATE config — the
    dry-run band the config page shows before Save commits anything.

    Uses the stored row INPUTS (gross, extras, miles, targets, days)
    and compares engine outputs: stored pct/dollars (what the snapshot
    pays today) vs the candidate's.  Overrides are ignored on both
    sides — the preview is about the RULES, not the exceptions.
    Nothing is written."""
    try:
        engine.validate_config(config)
        engine.validate_tiers(config.get("model", "ladder"), tiers)
    except ValueError as e:
        raise RunError(str(e))
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    runs = await tenant.list_kpi_runs(account_id)
    if not runs:
        return {"available": False}
    latest = runs[0]
    rows = await tenant.list_kpi_run_rows(account_id, latest["id"])
    current = 0.0
    candidate = 0.0
    moved = 0
    for r in rows:
        current = engine.money(current + float(r["kpi_dollars"]))
        if r.get("weekly_target") is None:
            cand_pct, cand_dollars = 0.0, 0.0
        else:
            active = max(0, int(r["total_days"]) - int(r["inactive_days"]))
            out = engine.compute_truck_row(
                config, tiers,
                base_gross=float(r["base_gross"]),
                extras=float(r["extras"]),
                miles=float(r["miles"]),
                weekly_target=float(r["weekly_target"]),
                active_days=active,
            )
            cand_pct, cand_dollars = out["pct"], out["kpi_dollars"]
        candidate = engine.money(candidate + cand_dollars)
        if float(cand_pct) != float(r["pct"]):
            moved += 1
    return {
        "available": True,
        "period_start": latest["period_start"],
        "period_end": latest["period_end"],
        "status": latest["status"],
        "rows": len(rows),
        "current_total": current,
        "candidate_total": candidate,
        "delta": engine.money(candidate - current),
        "moved_rows": moved,
    }


async def set_run_note(account_id: int, run_id: int, note: str) -> None:
    """The owner's one-line annotation ("226 in shop Thu-Fri").  A note
    is NOT money, so unlike every other write it is allowed on a
    FINALIZED run — annotating the paid record is how it stays
    explainable a month later."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    if await tenant.get_kpi_run(account_id, run_id) is None:
        raise RunError("run not found")
    await tenant.set_kpi_run_note(account_id, run_id, note.strip()[:300])


async def preview_run(account_id: int, *, period_start: str,
                      period_end: str) -> dict:
    """What a run for this period WOULD contain — scope before commit
    (trucks, dispatchers, gross, loads).  Read-only: nothing is created,
    no config needed (the preview is about the LOADS)."""
    loads = await loads_service.get_loads(
        account_id, since=period_start, until=period_end, limit=None,
    )
    keys = set()
    dispatchers = set()
    gross = 0.0
    count = 0
    for l in loads:
        if l.get("status") == "canceled":
            continue
        count += 1
        keys.add(_group_key(l))
        dispatchers.add(
            l.get("dispatcher_user_id") or f"name:{l.get('dispatcher_name')}")
        gross += float(l.get("total_rate") or 0)
    return {"loads": count, "trucks": len(keys),
            "dispatchers": len(dispatchers), "gross": engine.money(gross)}


async def discard_run(account_id: int, run_id: int,
                      *, discarded_by: int | None = None) -> None:
    """Discard a DRAFT run — the mistake-recovery path.  A draft is
    regenerable from the same period at any time, so discarding one loses
    only hand-entered adjustments; a FINALIZED run is the paid record and
    the storage layer refuses to delete it."""
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        raise RunError("tenant DB unavailable")
    run = await tenant.get_kpi_run(account_id, run_id)
    if run is None:
        raise RunError("run not found")
    if run["status"] != "draft":
        raise RunError(
            "this run is finalized — the paid record cannot be discarded")
    if not await tenant.delete_kpi_run(account_id, run_id):
        raise RunError("run not found")
    from capabilities.activity_trail import record_simple
    await record_simple(
        tenant, account_id, discarded_by, "delete", "kpi_run", run_id,
        context=None if discarded_by else {"system": "unattributed discard"},
        changes={"period": {
            "from": f"{str(run['period_start'])[:10]} – {str(run['period_end'])[:10]}",
            "to": None,
        }, "status": {"from": "draft", "to": None}},
    )
