"""Dispatcher GRADES — the A–D computation, pure.

Moved verbatim from ``features/kpi/service.py`` when the KPI root became
orchestration-only.  Grading (two-tier thresholds, account-configurable):
  A — every computable metric is GOOD
  B — nothing bad, but not all good
  C — exactly one metric is BAD
  D — two or more BAD metrics, or negative gross
A metric that can't be computed (no miles, no trucks) is neutral — it
neither helps nor hurts, so thin data doesn't produce dramatic grades.

This file is ANALYTICS, not money.  The incentive engine next door
(``engine.py``) computes compensation and is a different product with a
different permission story.
"""

from __future__ import annotations

from typing import Any


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
