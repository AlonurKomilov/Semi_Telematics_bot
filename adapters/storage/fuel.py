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

    async def get_fuel_summary(
        self, account_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Per-vehicle fuel summary: total gallons, total cost, avg price, entry count."""
        q = (
            "SELECT vehicle_name, company_code,"
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
        q += " GROUP BY vehicle_name ORDER BY total_cost DESC"
        cur = await self._db.execute(q, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
