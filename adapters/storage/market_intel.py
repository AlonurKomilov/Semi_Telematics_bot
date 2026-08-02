"""Anonymized market-price rollups (Phase D of
docs/architecture/vendor-parts-master-data.md).

The six hard rules, all enforced HERE so no caller can skip one:
  1. SHARING accounts only (accounts.share_market_data = 1) feed the
     computation — give-to-get consent is the input filter.
  2. A rollup row exists ONLY when >= 3 distinct companies contributed
     to that (shop, dimension) cell; below that an "aggregate" would be
     someone's actual invoice with the name removed.
  3. The published range is p25–p75 ("typical"), never raw min–max —
     one emergency road-call invoice must not stretch a shop's range
     into uselessness.
  4. 12-month rolling window on service_date.
  5. Fully anonymous: the rollup table stores counts and percentiles
     only — no account ids, no per-invoice rows, nothing joinable back.
  6. Keys on the GLOBAL directory identity (vendors.global_vendor_id),
     so name variants of one shop pool into one cell.

Dimensions:
  • service_task — price points are per-work-order parts totals for
    that task ("what a brake job's parts ran at this shop").
  • part — price points are per-line unit costs for catalog-linked
    parts ("what this shop charges for this part"), keyed on the
    normalized part name so it pools across accounts' catalogs.

Percentiles use the nearest-rank method on the sorted points
(index round((n-1) * q)) — deterministic, no interpolation, fine at
these sample sizes.
"""

from __future__ import annotations


_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})


def _us_state_from_address(address: str) -> str:
    """Best-effort state code from a curated directory address
    ("2540 High Point Pkwy, Barstow, CA, 92311" → "CA").  Scans
    comma-tokens from the END so street words never false-match;
    '' when nothing parses (the point still counts nationally)."""
    for token in reversed((address or "").split(",")):
        for word in reversed(token.strip().split()):
            w = word.strip().upper()
            if len(w) == 2 and w in _US_STATES:
                return w
    return ""


def _percentile(sorted_points: list[float], q: float) -> float:
    if not sorted_points:
        return 0.0
    idx = round((len(sorted_points) - 1) * q)
    return float(sorted_points[idx])


class MarketIntelMixin:

    MIN_COMPANIES = 3
    WINDOW_MONTHS = 12

    # ── Consent toggle ───────────────────────────────────────────

    async def set_market_sharing(
        self, account_id: int, enabled: bool,
        actor_user_id: "int | None" = None,
    ) -> bool:
        async with self.transaction():
            old = None
            if actor_user_id is not None:
                old = await self.get_market_sharing(account_id)
            cur = await self._db.execute(
                "UPDATE accounts SET share_market_data = ? WHERE id = ?",
                (1 if enabled else 0, account_id),
            )
            if cur.rowcount > 0 and actor_user_id is not None and old != enabled:
                await self.append_activity_events(account_id, [{
                    "entity_type": "sharing_settings",
                    "entity_id": "market_prices",
                    "action": "update", "actor_user_id": actor_user_id,
                    "changes": {"enabled": {"from": old, "to": enabled}},
                }])
            return cur.rowcount > 0

    async def get_market_sharing(self, account_id: int) -> bool:
        cur = await self._db.execute(
            "SELECT share_market_data FROM accounts WHERE id = ?",
            (account_id,),
        )
        row = await cur.fetchone()
        return bool(dict(row)["share_market_data"]) if row else False

    # ── Nightly rebuild ──────────────────────────────────────────

    async def compute_market_rollups(self, since_iso: str) -> int:
        """Full rebuild of ``market_price_rollups`` from sharing
        accounts' work orders newer than *since_iso* (the caller
        passes now − 12 months).  Returns rows written."""
        # Points per (shop, task): one point = one WO's parts total
        # for that task at that shop.
        cur = await self._db.execute(
            "SELECT v.global_vendor_id AS entry_id, "
            "       p.service_task     AS dim_key, "
            "       w.account_id       AS account_id, "
            "       w.id               AS wo_id, "
            "       SUM(p.total_cost)  AS point "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "JOIN vendors v ON v.id = w.vendor_id AND v.account_id = w.account_id "
            "JOIN accounts a ON a.id = w.account_id "
            "WHERE a.share_market_data = 1 "
            "  AND v.global_vendor_id IS NOT NULL "
            "  AND w.service_date IS NOT NULL AND w.service_date >= ? "
            "  AND p.service_task <> '' AND p.total_cost > 0 "
            "GROUP BY v.global_vendor_id, p.service_task, w.account_id, w.id",
            (since_iso,),
        )
        task_rows = [dict(r) for r in await cur.fetchall()]

        # Points per (shop, part): one point = one line's unit cost.
        # Key: the PUBLIC catalog identity when the account's part is
        # linked (``gp:<global_part_id>`` — pools "ConventionalWith
        # ClassicWsh" and "Truck Wash — Conventional" into ONE cell,
        # labeled with the canonical name), else the account catalog's
        # normalized name as before.  This keying was flipped BEFORE
        # MARKET_INTEL_ENABLED ever went live — re-keying after launch
        # would visibly shift published ranges (advisor rule).
        cur = await self._db.execute(
            "SELECT v.global_vendor_id AS entry_id, "
            "       CASE WHEN c.global_part_id IS NOT NULL "
            "            THEN 'gp:' || c.global_part_id::text "
            "            ELSE c.name_key END AS dim_key, "
            "       COALESCE(NULLIF(d.name, ''), c.name) AS dim_label, "
            "       c.global_part_id   AS global_part_id, "
            "       c.name_key         AS raw_key, "
            "       vd.address         AS shop_address, "
            "       w.account_id       AS account_id, "
            "       p.unit_cost        AS point "
            "FROM work_order_parts p "
            "JOIN work_orders w ON w.id = p.work_order_id "
            "JOIN vendors v ON v.id = w.vendor_id AND v.account_id = w.account_id "
            "JOIN parts_catalog c ON c.id = p.part_id AND c.account_id = w.account_id "
            "LEFT JOIN part_directory d ON d.id = c.global_part_id "
            "LEFT JOIN vendor_directory vd ON vd.id = v.global_vendor_id "
            "JOIN accounts a ON a.id = w.account_id "
            "WHERE a.share_market_data = 1 "
            "  AND v.global_vendor_id IS NOT NULL "
            "  AND w.service_date IS NOT NULL AND w.service_date >= ? "
            "  AND p.unit_cost > 0",
            (since_iso,),
        )
        # Unlinked GENERIC names never form cells: "labor" / "shop
        # supplies" mean a different thing at every shop, so pooling
        # their unit costs is exactly the unlike-things poisoning the
        # blocklist exists to prevent.  (Linked parts can't be generic
        # — entry creation rejects the blocklist.)
        from .part_directory import GENERIC_PART_KEYS
        part_rows = [
            r for r in (dict(x) for x in await cur.fetchall())
            if r.get("global_part_id") or r.get("raw_key") not in GENERIC_PART_KEYS
        ]

        # Aggregate in Python (portable percentiles).
        cells: dict = {}
        for r in task_rows:
            k = (int(r["entry_id"]), "service_task", str(r["dim_key"]))
            c = cells.setdefault(k, {"accounts": set(), "points": [], "labels": {}})
            c["accounts"].add(r["account_id"])
            c["points"].append(float(r["point"]))
        for r in part_rows:
            k = (int(r["entry_id"]), "part", str(r["dim_key"]))
            c = cells.setdefault(k, {"accounts": set(), "points": [], "labels": {}})
            c["accounts"].add(r["account_id"])
            c["points"].append(float(r["point"]))
            lbl = str(r.get("dim_label") or "")
            if lbl:
                c["labels"][lbl] = c["labels"].get(lbl, 0) + 1

        # ── Part-centric GEOGRAPHIC cells (owner ask: "what should
        # this part cost around me?" — consulted BEFORE picking a
        # shop, unlike the per-shop cells above which answer "is this
        # quote fair?" at the shop).  ONLY catalog-linked parts pool
        # here (canonical identity across accounts); the shop's
        # curated address supplies the state, national always counts.
        # City tier deliberately deferred: 3+ sharing companies per
        # CITY per part is years away — state cells light up first.
        geo_cells: dict = {}
        for r in part_rows:
            gpid = r.get("global_part_id")
            if not gpid:
                continue
            for scope, region in (
                ("national", ""),
                ("state", _us_state_from_address(str(r.get("shop_address") or ""))),
            ):
                if scope == "state" and not region:
                    continue
                k = (int(gpid), scope, region)
                c = geo_cells.setdefault(k, {"accounts": set(), "points": []})
                c["accounts"].add(r["account_id"])
                c["points"].append(float(r["point"]))

        now = self._now()
        await self._db.execute("DELETE FROM market_price_rollups")
        await self._db.execute("DELETE FROM market_part_rollups")
        for (gpid, scope, region), c in geo_cells.items():
            if len(c["accounts"]) < self.MIN_COMPANIES:
                continue          # same rule 2 — never publish thin cells
            pts = sorted(c["points"])
            await self._db.execute(
                "INSERT INTO market_part_rollups "
                "(global_part_id, scope, region, companies, invoices, "
                " p25, p75, window_months, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (gpid, scope, region, len(c["accounts"]), len(pts),
                 round(_percentile(pts, 0.25), 2),
                 round(_percentile(pts, 0.75), 2), self.WINDOW_MONTHS, now),
            )
        written = 0
        for (entry_id, dim_type, dim_key), c in cells.items():
            if len(c["accounts"]) < self.MIN_COMPANIES:
                continue          # rule 2 — the one that makes it safe
            pts = sorted(c["points"])
            label = (max(c["labels"].items(), key=lambda kv: kv[1])[0]
                     if c["labels"] else "")
            await self._db.execute(
                "INSERT INTO market_price_rollups "
                "(entry_id, dim_type, dim_key, dim_label, companies, "
                " invoices, p25, p75, window_months, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, dim_type, dim_key, label, len(c["accounts"]),
                 len(pts), round(_percentile(pts, 0.25), 2),
                 round(_percentile(pts, 0.75), 2), self.WINDOW_MONTHS, now),
            )
            written += 1
        await self._db.commit()
        return written

    # ── Reads ────────────────────────────────────────────────────

    async def market_part_estimates(self, global_part_id: int) -> dict:
        """Published geographic estimates for one catalog part:
        the national cell (or None) + every state cell that passed
        the 3-company rule.  Published shape only."""
        cur = await self._db.execute(
            "SELECT scope, region, companies, invoices, p25, p75, "
            "       window_months, computed_at "
            "FROM market_part_rollups WHERE global_part_id = ? "
            "ORDER BY scope ASC, region ASC",
            (global_part_id,),
        )
        national, states = None, []
        for r in (dict(x) for x in await cur.fetchall()):
            if r["scope"] == "national":
                national = r
            else:
                states.append(r)
        return {"national": national, "states": states}

    async def market_part_national_map(self) -> dict[int, dict]:
        """All national part cells in one read — the Catalog browse
        column's data source (one query, merged in the handler)."""
        cur = await self._db.execute(
            "SELECT global_part_id, companies, p25, p75 "
            "FROM market_part_rollups WHERE scope = 'national'",
        )
        return {
            int(r["global_part_id"]): dict(r)
            for r in (dict(x) for x in await cur.fetchall())
        }

    async def market_intel_stats(self) -> dict:
        """Console readiness numbers: is the flywheel turning?"""
        out: dict = {}
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n FROM accounts WHERE share_market_data = 1",
        )
        out["sharing_accounts"] = int(dict(await cur.fetchone())["n"])
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n, MAX(computed_at) AS at FROM market_price_rollups",
        )
        row = dict(await cur.fetchone())
        out["vendor_cells"] = int(row["n"] or 0)
        out["computed_at"] = row["at"]
        cur = await self._db.execute(
            "SELECT COUNT(*) AS n FROM market_part_rollups",
        )
        out["part_geo_cells"] = int(dict(await cur.fetchone())["n"])
        return out

    async def market_rollups_for_entry(self, entry_id: int) -> list[dict]:
        """Published shape only — counts + typical range, nothing
        joinable back to any account."""
        cur = await self._db.execute(
            "SELECT dim_type, dim_key, dim_label, companies, invoices, "
            "       p25, p75, window_months, computed_at "
            "FROM market_price_rollups WHERE entry_id = ? "
            "ORDER BY dim_type ASC, invoices DESC",
            (entry_id,),
        )
        return [dict(r) for r in await cur.fetchall()]
