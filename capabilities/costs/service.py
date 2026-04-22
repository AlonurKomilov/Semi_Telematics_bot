"""Cost service — Single Source of Truth for cost-per-mile calculations.

Both bot handlers and API routes call these functions instead of
duplicating the CPM / MPG math.
"""

from __future__ import annotations


def compute_vehicle_cpm(fuel_summary_row: dict) -> dict:
    """Compute cost-per-mile and MPG for a single vehicle fuel summary row.

    Returns a dict with: truck, company, miles, cost, gallons, cpm, mpg.
    """
    total_cost = fuel_summary_row.get("total_cost") or 0
    first_odo = fuel_summary_row.get("first_odo") or 0
    last_odo = fuel_summary_row.get("last_odo") or 0
    total_gal = fuel_summary_row.get("total_gallons") or 0
    miles = last_odo - first_odo if last_odo > first_odo else 0
    cpm = total_cost / miles if miles > 0 else 0
    mpg = miles / total_gal if total_gal > 0 and miles > 0 else 0

    return {
        "truck": fuel_summary_row.get("vehicle_name", ""),
        "company": fuel_summary_row.get("company_code", ""),
        "miles": round(miles),
        "cost": round(total_cost, 2),
        "gallons": round(total_gal, 1),
        "cpm": round(cpm, 2),
        "mpg": round(mpg, 1),
    }


def compute_fleet_cpm(fuel_summary: list[dict]) -> tuple[list[dict], dict]:
    """Compute CPM for every vehicle and fleet-wide averages.

    Returns (vehicle_results, fleet_summary) where fleet_summary has:
        fleet_cost, fleet_miles, fleet_gallons, fleet_cpm, fleet_mpg.
    """
    results = [compute_vehicle_cpm(row) for row in fuel_summary]

    fleet_cost = sum(r["cost"] for r in results)
    fleet_miles = sum(r["miles"] for r in results)
    fleet_gallons = sum(r["gallons"] for r in results)
    fleet_cpm = fleet_cost / fleet_miles if fleet_miles > 0 else 0
    fleet_mpg = fleet_miles / fleet_gallons if fleet_gallons > 0 else 0

    fleet = {
        "fleet_cost": round(fleet_cost, 2),
        "fleet_miles": round(fleet_miles),
        "fleet_gallons": round(fleet_gallons, 1),
        "fleet_cpm": round(fleet_cpm, 2),
        "fleet_mpg": round(fleet_mpg, 1),
    }
    return results, fleet


def summarize_fuel_entries(rows: list[dict]) -> dict:
    """Compute per-vehicle and fleet fuel summary including miles + MPG.

    Accepts rows from ``tenant_db.get_fuel_summary()`` — same format as
    ``compute_fleet_cpm`` input — and returns the complete summary shape
    used by both bot/fuel_costs.py and api/costs.py.
    """
    items: list[dict] = []
    fleet_cost = 0.0
    fleet_gallons = 0.0
    fleet_miles = 0.0

    for r in rows:
        total_cost = r.get("total_cost") or 0
        total_gal = r.get("total_gallons") or 0
        avg_price = r.get("avg_price") or 0
        first_odo = r.get("first_odo") or 0
        last_odo = r.get("last_odo") or 0
        miles = last_odo - first_odo if last_odo > first_odo else 0
        mpg = miles / total_gal if total_gal > 0 and miles > 0 else 0

        fleet_cost += total_cost
        fleet_gallons += total_gal
        fleet_miles += miles

        items.append({
            "vehicle_name": r.get("vehicle_name", ""),
            "company": r.get("company_code", ""),
            "entries": r.get("entries", 0),
            "total_gallons": round(total_gal, 1),
            "total_cost": round(total_cost, 2),
            "avg_price": round(avg_price, 3),
            "first_odo": first_odo,
            "last_odo": last_odo,
            "miles": round(miles),
            "mpg": round(mpg, 1),
        })

    return {
        "vehicles": items,
        "count": len(items),
        "fleet_total_cost": round(fleet_cost, 2),
        "fleet_total_gallons": round(fleet_gallons, 1),
        "fleet_total_miles": round(fleet_miles),
    }
