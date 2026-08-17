"""Dispatcher incentive config — storage mixin.

Tables (migration 193):
  - kpi_incentive_config   one row per account; the tier table is JSON on
                           the row because a ladder is ONE value, edited
                           as a set (see the migration's rationale)
  - kpi_company_targets    weekly gross bar per company, UNIQUE per
                           (account, company) so one company cannot carry
                           two targets — the exact hand-entry mistake the
                           customer's Excel made

Validation does NOT live here.  The engine owns what a legal config is
(``features/kpi/dispatch/engine.py``), the endpoint calls it before
writing; this mixin stores and retrieves faithfully.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class."""
        _db: Any

        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


_CONFIG_FIELDS = (
    "model", "combine_rule", "calc_cadence", "calc_custom_days",
    "exception_cap_pct", "floor_weekly_gross", "floor_rpm",
)


class KpiIncentivesMixin(_MixinBase):

    # ── the config row ────────────────────────────────────────────────

    async def get_kpi_incentive_config(
        self, account_id: int,
    ) -> Optional[dict]:
        """The account's incentive config with tiers parsed, or None
        when the account has never configured incentives."""
        cur = await self._db.execute(
            "SELECT model, combine_rule, calc_cadence, calc_custom_days, "
            "exception_cap_pct, floor_weekly_gross, floor_rpm, tiers, "
            "updated_by, updated_at "
            "FROM kpi_incentive_config WHERE account_id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        out = dict(zip((
            "model", "combine_rule", "calc_cadence", "calc_custom_days",
            "exception_cap_pct", "floor_weekly_gross", "floor_rpm",
            "tiers", "updated_by", "updated_at",
        ), tuple(row)))
        try:
            out["tiers"] = json.loads(out["tiers"] or "[]")
        except (TypeError, ValueError):
            # A corrupt row must not brick the config page; the editor
            # shows an empty table and the next save repairs it.
            out["tiers"] = []
        if not isinstance(out["tiers"], list):
            out["tiers"] = []
        return out

    async def set_kpi_incentive_config(
        self, account_id: int, values: dict, tiers: list[dict],
        *, updated_by: int,
    ) -> None:
        """Upsert the account's config row.  ``values`` may carry any of
        the config fields; missing ones keep their column defaults on
        insert and their stored values on update."""
        now = self._now()
        existing = await self.get_kpi_incentive_config(account_id)
        merged = {f: values.get(f, (existing or {}).get(f)) for f in _CONFIG_FIELDS}
        if merged["model"] is None:
            merged["model"] = "ladder"
        if merged["combine_rule"] is None:
            merged["combine_rule"] = "lower"
        if merged["calc_cadence"] is None:
            merged["calc_cadence"] = "weekly"
        await self._db.execute(
            "INSERT INTO kpi_incentive_config "
            "(account_id, model, combine_rule, calc_cadence, "
            " calc_custom_days, exception_cap_pct, floor_weekly_gross, "
            " floor_rpm, tiers, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(account_id) DO UPDATE SET "
            " model=excluded.model, combine_rule=excluded.combine_rule, "
            " calc_cadence=excluded.calc_cadence, "
            " calc_custom_days=excluded.calc_custom_days, "
            " exception_cap_pct=excluded.exception_cap_pct, "
            " floor_weekly_gross=excluded.floor_weekly_gross, "
            " floor_rpm=excluded.floor_rpm, tiers=excluded.tiers, "
            " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (
                account_id, merged["model"], merged["combine_rule"],
                merged["calc_cadence"], merged["calc_custom_days"],
                merged["exception_cap_pct"], merged["floor_weekly_gross"],
                merged["floor_rpm"], json.dumps(tiers),
                updated_by, now,
            ),
        )
        await self._db.commit()

    # ── per-company targets ───────────────────────────────────────────

    async def list_kpi_company_targets(self, account_id: int) -> list[dict]:
        """Targets joined to their company code/name — what the editor
        renders and what a run resolves a load's company_code against."""
        cur = await self._db.execute(
            "SELECT t.company_id, c.code, c.display_name, "
            "t.weekly_gross_target "
            "FROM kpi_company_targets t "
            "JOIN companies c ON c.id = t.company_id "
            "WHERE t.account_id = ? ORDER BY c.code",
            (account_id,),
        )
        return [
            {
                "company_id": r[0], "company_code": r[1],
                "company_name": r[2], "weekly_gross_target": r[3],
            }
            for r in await cur.fetchall()
        ]

    async def set_kpi_company_targets(
        self, account_id: int, targets: dict[int, float],
    ) -> None:
        """Replace the target set.  A company absent from ``targets``
        loses its row — the editor submits the whole table, and a company
        with no target simply has no incentive bar (its trucks resolve
        pct 0 until one is set), which is visible rather than stale."""
        await self._db.execute(
            "DELETE FROM kpi_company_targets WHERE account_id = ?",
            (account_id,),
        )
        for company_id, target in targets.items():
            await self._db.execute(
                "INSERT INTO kpi_company_targets "
                "(account_id, company_id, weekly_gross_target) "
                "VALUES (?, ?, ?)",
                (account_id, int(company_id), float(target)),
            )
        await self._db.commit()

    # ── runs (migration 194) ──────────────────────────────────────────

    _ROW_COLS = (
        "id", "run_id", "account_id", "dispatcher_user_id",
        "dispatcher_name", "company_code", "vehicle_unit",
        "window_start", "window_end", "total_days", "inactive_days",
        "inactive_reason", "inactive_dates", "base_gross", "extras",
        "extras_note", "miles",
        "weekly_target", "kpi_gross", "rpm", "adjusted_target", "pct",
        "kpi_dollars", "override_pct", "override_reason", "zero_reason",
        "confirmed_dollars", "confirmed_by", "confirmed_at",
    )

    async def create_kpi_run(
        self, account_id: int, *, period_start: str, period_end: str,
        config_snapshot: str, created_by: int,
    ) -> int:
        cur = await self._db.execute(
            "INSERT INTO kpi_incentive_runs "
            "(account_id, period_start, period_end, status, "
            " config_snapshot, created_by, created_at) "
            "VALUES (?, ?, ?, 'draft', ?, ?, ?) RETURNING id",
            (account_id, period_start, period_end, config_snapshot,
             created_by, self._now()),
        )
        row = await cur.fetchone()
        await self._db.commit()
        return int(row[0])

    # Columns that are NOT NULL DEFAULT — an EXPLICIT NULL in an INSERT
    # bypasses a column default, so absent keys must land as the default
    # value, not as None.  Genuinely nullable columns (weekly_target,
    # rpm, override_pct, confirmed_by, dispatcher_user_id) are absent
    # from this map on purpose.
    _ROW_TEXT_DEFAULTS = (
        "dispatcher_name", "company_code", "vehicle_unit", "window_start",
        "window_end", "inactive_reason", "extras_note", "override_reason",
        "zero_reason", "confirmed_at",
    )
    _ROW_NUM_DEFAULTS = (
        "total_days", "inactive_days", "base_gross", "extras", "miles",
        "kpi_gross", "adjusted_target", "pct", "kpi_dollars",
        "confirmed_dollars",
    )

    def _row_value(self, col: str, row: dict):
        v = row.get(col)
        if v is None and col in self._ROW_TEXT_DEFAULTS:
            return ""
        if v is None and col == "inactive_dates":
            # JSON-list column: its empty value is '[]', not ''.
            return "[]"
        if v is None and col in self._ROW_NUM_DEFAULTS:
            return 0
        return v

    async def insert_kpi_run_row(self, run_id: int, account_id: int,
                                 row: dict) -> int:
        cols = [c for c in self._ROW_COLS if c not in ("id", "run_id",
                                                       "account_id")]
        cur = await self._db.execute(
            f"INSERT INTO kpi_incentive_run_rows (run_id, account_id, "
            f"{', '.join(cols)}) VALUES (?, ?, "
            f"{', '.join('?' for _ in cols)}) RETURNING id",
            (run_id, account_id, *(self._row_value(c, row) for c in cols)),
        )
        rid = int((await cur.fetchone())[0])
        await self._db.commit()
        return rid

    async def get_kpi_run(self, account_id: int, run_id: int):
        cur = await self._db.execute(
            "SELECT id, period_start, period_end, status, config_snapshot, "
            "created_by, created_at, finalized_by, finalized_at "
            "FROM kpi_incentive_runs WHERE account_id = ? AND id = ?",
            (account_id, run_id),
        )
        r = await cur.fetchone()
        if r is None:
            return None
        return dict(zip((
            "id", "period_start", "period_end", "status", "config_snapshot",
            "created_by", "created_at", "finalized_by", "finalized_at",
        ), tuple(r)))

    async def list_kpi_runs(self, account_id: int) -> list[dict]:
        # total + row_count ride along for the runs ARCHIVE grid — one
        # aggregate join instead of N per-run row queries.
        cur = await self._db.execute(
            "SELECT r.id, r.period_start, r.period_end, r.status, "
            "r.created_at, r.finalized_at, "
            "COALESCE(SUM(w.confirmed_dollars), 0) AS total, "
            "COUNT(w.id) AS row_count "
            "FROM kpi_incentive_runs r "
            "LEFT JOIN kpi_incentive_run_rows w ON w.run_id = r.id "
            "WHERE r.account_id = ? "
            "GROUP BY r.id, r.period_start, r.period_end, r.status, "
            "r.created_at, r.finalized_at "
            "ORDER BY r.period_start DESC, r.id DESC",
            (account_id,),
        )
        return [dict(zip(("id", "period_start", "period_end", "status",
                          "created_at", "finalized_at", "total",
                          "row_count"), tuple(r)))
                for r in await cur.fetchall()]

    async def list_kpi_run_rows(self, account_id: int,
                                run_id: int) -> list[dict]:
        cur = await self._db.execute(
            f"SELECT {', '.join(self._ROW_COLS)} "
            "FROM kpi_incentive_run_rows "
            "WHERE account_id = ? AND run_id = ? "
            "ORDER BY dispatcher_name, company_code, vehicle_unit",
            (account_id, run_id),
        )
        return [dict(zip(self._ROW_COLS, tuple(r)))
                for r in await cur.fetchall()]

    async def get_kpi_run_row(self, account_id: int, run_id: int,
                              row_id: int):
        cur = await self._db.execute(
            f"SELECT {', '.join(self._ROW_COLS)} "
            "FROM kpi_incentive_run_rows "
            "WHERE account_id = ? AND run_id = ? AND id = ?",
            (account_id, run_id, row_id),
        )
        r = await cur.fetchone()
        return None if r is None else dict(zip(self._ROW_COLS, tuple(r)))

    async def update_kpi_run_row(self, account_id: int, run_id: int,
                                 row_id: int, fields: dict) -> None:
        allowed = [c for c in self._ROW_COLS
                   if c not in ("id", "run_id", "account_id")]
        sets = [f"{k} = ?" for k in fields if k in allowed]
        if not sets:
            return
        await self._db.execute(
            f"UPDATE kpi_incentive_run_rows SET {', '.join(sets)} "
            "WHERE account_id = ? AND run_id = ? AND id = ?",
            (*(v for k, v in fields.items() if k in allowed),
             account_id, run_id, row_id),
        )
        await self._db.commit()

    async def finalize_kpi_run(self, account_id: int, run_id: int,
                               finalized_by: int) -> None:
        await self._db.execute(
            "UPDATE kpi_incentive_runs SET status='finalized', "
            "finalized_by = ?, finalized_at = ? "
            "WHERE account_id = ? AND id = ? AND status = 'draft'",
            (finalized_by, self._now(), account_id, run_id),
        )
        await self._db.commit()

    async def delete_kpi_run(self, account_id: int, run_id: int) -> bool:
        """Discard a DRAFT run and its rows.  Refuses finalized runs at
        the SQL level — the WHERE clause is the guard, so a race between
        finalize and discard cannot delete the paid record."""
        await self._db.execute(
            "DELETE FROM kpi_incentive_run_rows WHERE account_id = ? "
            "AND run_id = ? AND run_id IN (SELECT id FROM "
            "kpi_incentive_runs WHERE account_id = ? AND status = 'draft')",
            (account_id, run_id, account_id),
        )
        cur = await self._db.execute(
            "DELETE FROM kpi_incentive_runs WHERE account_id = ? "
            "AND id = ? AND status = 'draft' RETURNING id",
            (account_id, run_id),
        )
        deleted = (await cur.fetchone()) is not None
        await self._db.commit()
        return deleted
