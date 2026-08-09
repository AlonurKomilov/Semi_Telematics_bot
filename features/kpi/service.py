"""KPI feature — the computation contract.

``compute_dispatcher_kpis`` is a PURE function over serialized loads so the
math + grading are unit-testable without a database; the IO wrappers read
loads through ``features.loads.service`` (service-contract rule) and the
thresholds through ``account_settings``.

Grading (two-tier thresholds, account-configurable):
  A — every computable metric is GOOD
  B — nothing bad, but not all good
  C — exactly one metric is BAD
  D — two or more BAD metrics, or negative gross
A metric that can't be computed (no miles, no trucks) is neutral — it
neither helps nor hurts, so thin data doesn't produce dramatic grades.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from features.loads import service as loads_service
from infra.platform import get_tenant_db

KPI_SETTING_KEY = "kpi_thresholds"

# Defaults from the operating rules the platform ships with; every value is
# per-account overridable in the KPI page's thresholds dialog.
DEFAULT_KPI_THRESHOLDS: dict[str, float] = {
    "rpm_good": 2.30,
    "rpm_bad": 2.00,
    "empty_pct_good": 10.0,
    "empty_pct_bad": 15.0,
    "gross_per_truck_good": 7500.0,
    "gross_per_truck_bad": 6500.0,
}


def grade(m: dict, t: dict[str, float]) -> str:
    """A–D per the module docstring.  ``m`` is one dispatcher's metrics."""
    goods = 0
    bads = 0
    computable = 0

    def _judge(value, good, bad, higher_is_better: bool) -> None:
        nonlocal goods, bads, computable
        if value is None:
            return
        computable += 1
        if higher_is_better:
            if value >= good:
                goods += 1
            elif value < bad:
                bads += 1
        else:
            if value <= good:
                goods += 1
            elif value > bad:
                bads += 1

    _judge(m.get("rpm"), t["rpm_good"], t["rpm_bad"], True)
    _judge(m.get("empty_pct"), t["empty_pct_good"], t["empty_pct_bad"], False)
    _judge(m.get("gross_per_truck"),
           t["gross_per_truck_good"], t["gross_per_truck_bad"], True)

    gross = m.get("gross")
    if (gross is not None and gross < 0) or bads >= 2:
        return "D"
    if bads == 1:
        return "C"
    if computable > 0 and goods == computable:
        return "A"
    return "B"


def compute_dispatcher_kpis(
    loads: list[dict], thresholds: dict[str, float],
    off_load_items: list[dict] | None = None,
) -> list[dict]:
    """Group serialized loads by dispatcher and compute the KPI row for
    each.  Canceled loads don't count (no revenue was earned).  Dispatchers
    are keyed by linked user id when present, else by name — so TMS-synced
    dispatchers rank correctly before they're 4truck users.

    ``off_load_items`` are the no-load line items (layover pay: a driver
    sat because dispatch found no load) — charged to the attributed
    dispatcher's costs even though no load exists to carry them."""
    groups: dict[Any, dict] = {}
    for l in loads:
        if l.get("status") == "canceled":
            continue
        key = l.get("dispatcher_user_id") or \
            f"name:{(l.get('dispatcher_name') or '').strip().lower()}"
        if key == "name:":
            key = "name:(unassigned)"
        g = groups.setdefault(key, {
            "dispatcher_user_id": l.get("dispatcher_user_id"),
            "dispatcher_name": (l.get("dispatcher_name") or "").strip()
                               or "(unassigned)",
            "revenue": 0.0, "loaded_miles": 0.0, "empty_miles": 0.0,
            "driver_pay": 0.0, "other_costs": 0.0,
            "loads": 0, "_trucks": set(), "_drivers": set(),
        })
        g["revenue"] += float(l.get("total_rate") or 0)
        g["loaded_miles"] += float(l.get("loaded_miles") or 0)
        g["empty_miles"] += float(l.get("empty_miles") or 0)
        # Extra pay & costs (line items: TONU / bonus / tolls / …) ride
        # into the same buckets the gross formula subtracts.
        g["driver_pay"] += float(l.get("driver_pay") or 0) \
            + float(l.get("extra_driver_pay") or 0)
        g["other_costs"] += float(l.get("other_costs") or 0) \
            + float(l.get("extra_costs") or 0)
        g["loads"] += 1
        if l.get("vehicle_unit"):
            g["_trucks"].add(str(l["vehicle_unit"]))
        if l.get("driver_user_id") or l.get("driver_name"):
            g["_drivers"].add(str(l.get("driver_user_id") or l.get("driver_name")))

    for item in (off_load_items or []):
        uid = item.get("dispatcher_user_id")
        key = uid or "name:(unassigned)"
        g = groups.setdefault(key, {
            "dispatcher_user_id": uid,
            "dispatcher_name": f"(user #{uid})" if uid else "(unassigned)",
            "revenue": 0.0, "loaded_miles": 0.0, "empty_miles": 0.0,
            "driver_pay": 0.0, "other_costs": 0.0,
            "loads": 0, "_trucks": set(), "_drivers": set(),
        })
        bucket = "driver_pay" if item.get("bucket") == "driver_pay" \
            else "other_costs"
        g[bucket] += float(item.get("amount") or 0)

    out: list[dict] = []
    for g in groups.values():
        total_miles = g["loaded_miles"] + g["empty_miles"]
        trucks = len(g.pop("_trucks"))
        drivers = len(g.pop("_drivers"))
        gross = g["revenue"] - g["driver_pay"] - g["other_costs"]
        m = {
            **g,
            "total_miles": round(total_miles, 1) or None,
            "empty_pct": round(g["empty_miles"] / total_miles * 100, 1)
                         if total_miles > 0 else None,
            "rpm": round(g["revenue"] / total_miles, 2)
                   if total_miles > 0 and g["revenue"] else None,
            "gross": round(gross, 2) if g["revenue"] else None,
            "trucks": trucks, "drivers": drivers,
            "revenue_per_truck": round(g["revenue"] / trucks, 2)
                                 if trucks else None,
            "gross_per_truck": round(gross / trucks, 2)
                               if trucks and g["revenue"] else None,
        }
        m["revenue"] = round(m["revenue"], 2)
        m["grade"] = grade(m, thresholds)
        out.append(m)
    out.sort(key=lambda r: r["revenue"], reverse=True)
    return out


# ── Thresholds (account-configurable) ──────────────────────────────────


async def get_kpi_thresholds(db: Any, account_id: int) -> dict[str, float]:
    """The account's thresholds merged over the defaults; unknown / invalid
    override keys fall back."""
    merged = dict(DEFAULT_KPI_THRESHOLDS)
    try:
        raw = await db.get_account_setting(account_id, KPI_SETTING_KEY, "")
    except Exception:
        raw = ""
    if raw:
        try:
            override = json.loads(raw)
        except (TypeError, ValueError):
            override = None
        if isinstance(override, dict):
            for k, v in override.items():
                if k in merged:
                    try:
                        merged[k] = float(v)
                    except (TypeError, ValueError):
                        pass
    return merged


async def set_kpi_thresholds(
    db: Any, account_id: int, values: dict,
) -> dict[str, float]:
    """Persist overrides (known keys only, coerced to float)."""
    clean: dict[str, float] = {}
    for k, v in (values or {}).items():
        if k in DEFAULT_KPI_THRESHOLDS:
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                continue
    await db.set_account_setting(account_id, KPI_SETTING_KEY, json.dumps(clean))
    return await get_kpi_thresholds(db, account_id)


# ── IO wrapper (what the router serves) ────────────────────────────────


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
