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
