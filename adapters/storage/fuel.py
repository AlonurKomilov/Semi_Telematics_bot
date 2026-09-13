"""Fuel entries CRUD mixin."""

from __future__ import annotations

from typing import Optional


class FuelMixin:

    async def add_fuel_entry(
        self, account_id: int, company_code: str,
        vehicle_name: str, gallons: float, price_per_gallon: float,
        odometer_miles: float, date: str,
        created_by: int = 0, vehicle_id: str = "",
    ) -> int:
        now = self._now()
        total_cost = round(gallons * price_per_gallon, 2)
        cur = await self._db.execute(
            """INSERT INTO fuel_entries
               (account_id, company_code, vehicle_id, vehicle_name,
                gallons, price_per_gallon, total_cost, odometer_miles,
                date, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, company_code, vehicle_id, vehicle_name,
             gallons, price_per_gallon, total_cost, odometer_miles,
             date, created_by, now),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_fuel_entries(
        self, account_id: int, vehicle_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        q = "SELECT * FROM fuel_entries WHERE account_id = ?"
        params: list = [account_id]
        if vehicle_name:
            q += " AND vehicle_name = ?"
            params.append(vehicle_name)
        q += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_fuel_entry_stats(self, account_id: int) -> dict:
        """Account-wide fuel-entry existence stats: total entry count and
        the first/last entry dates.  One cheap aggregate — lets an empty
        WINDOW answer distinguish "nothing in that period (data exists,
        newest on X)" from "this account has never recorded fuel data",
        which need completely different user guidance."""
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n, MIN(date) AS first_date, MAX(date) AS last_date"
            " FROM fuel_entries WHERE account_id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        d = dict(row) if row else {}
        return {
            "count": int(d.get("n") or 0),
            "first_date": d.get("first_date") or "",
            "last_date": d.get("last_date") or "",
        }

    async def get_fuel_summary(
        self, account_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Per-vehicle fuel summary: total gallons, total cost, avg price, entry count.

        Grouped by (vehicle_name, company_code), NOT by name alone.

        Unit numbers are reused across companies, so grouping by name
        merged two different trucks into one row — their gallons, their
        cost and their odometer range added together. That is wrong
        money on its face, and it also merged them BEFORE any caller
        could separate them, so a company-scoped reader saw the other
        company's spend inside their own row with nothing to filter on.
        The old comment called this a rare post-re-org edge case; twins
        are a standing fact of this data.

        ``MAX(vehicle_id)`` carries the provider id onto the row so the
        identity ladder can decide it. Entries typed by hand store an
        empty one, and MAX picks the non-empty value where a truck has
        both — a truck's rows stay together while the twins split.
        """
        q = (
            "SELECT vehicle_name,"
            " company_code,"
            " MAX(vehicle_id) as vehicle_id,"
            " COUNT(*) as entries,"
            " SUM(gallons) as total_gallons,"
            " SUM(total_cost) as total_cost,"
            " AVG(price_per_gallon) as avg_price,"
            " MIN(odometer_miles) as first_odo,"
            " MAX(odometer_miles) as last_odo"
            " FROM fuel_entries WHERE account_id = ?"
        )
        params: list = [account_id]
        if start_date:
            q += " AND date >= ?"
            params.append(start_date)
        if end_date:
            q += " AND date <= ?"
            params.append(end_date)
        q += (" GROUP BY vehicle_name, company_code"
              " ORDER BY total_cost DESC")
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
