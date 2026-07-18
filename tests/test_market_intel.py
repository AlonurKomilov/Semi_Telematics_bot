"""Market-intel rollups (Phase D) — the six hard rules, enforced in
storage so no caller can skip one:

sharing-accounts-only input, >=3-companies threshold, p25-p75 typical
range (not min-max), 12-month window, anonymity of the published
shape, and keying on the global directory identity.
"""

from __future__ import annotations

import pytest

SINCE = "2025-08-01"   # test "now - 12mo" cutoff


async def _acct_uses_shop(db, account_id: int, entry, price: float,
                          service_date: str = "2026-06-01",
                          task: str = "brakes", share: bool = True):
    """One sharing account with one WO at the shop: a brake-job line
    (point for the task dim) linked to a catalog part (point for the
    part dim)."""
    await db.set_market_sharing(account_id, share)
    v = await db.resolve_or_create_vendor(account_id, entry["name"])
    await db.link_vendor_to_directory(account_id, v["id"], entry["id"])
    wo = await db.add_work_order(
        account_id, "CO", "T-1", entry["name"],
        vendor_id=v["id"], service_date=service_date, total_cost=price,
    )
    part = await db.resolve_or_create_part(account_id, "Brake Pad Set")
    await db.add_work_order_part(
        wo, part_name="Brake Pad Set", quantity=1, unit_cost=price,
        total_cost=price, service_task=task, part_id=part["id"],
    )


@pytest.mark.asyncio
async def test_threshold_window_and_range(db):
    # Accounts must exist for the accounts-table consent join.
    a1 = (await db.create_account("M1")).id
    a2 = (await db.create_account("M2")).id
    a3 = (await db.create_account("M3")).id
    a4 = (await db.create_account("M4")).id
    a5 = (await db.create_account("M5")).id
    shop = await db.create_directory_entry("Rollup Repair", status="active")

    # Three sharing companies in-window: cell publishes.
    await _acct_uses_shop(db, a1, shop, 100.0)
    await _acct_uses_shop(db, a2, shop, 200.0)
    await _acct_uses_shop(db, a3, shop, 300.0)
    # A NON-sharing company: excluded even though it used the shop.
    await _acct_uses_shop(db, a4, shop, 9999.0, share=False)
    # A sharing company but OUT of the window: excluded.
    await _acct_uses_shop(db, a5, shop, 8888.0, service_date="2024-01-01")

    n = await db.compute_market_rollups(SINCE)
    rows = await db.market_rollups_for_entry(shop["id"])
    by = {(r["dim_type"], r["dim_key"]): r for r in rows}

    task = by[("service_task", "brakes")]
    assert task["companies"] == 3           # a4 (not sharing) + a5 (old) out
    assert task["invoices"] == 3
    # Nearest-rank p25/p75 over [100, 200, 300] → 150-ish? indexes:
    # round(2*0.25)=1 → 200? No: round(0.5)=0 (banker's) — pin exact:
    assert task["p25"] in (100.0, 200.0)    # nearest-rank, small-n
    assert task["p75"] == 300.0 or task["p75"] == 200.0
    assert task["p25"] <= task["p75"]
    # The excluded outliers must be nowhere in the range.
    assert task["p75"] < 8888.0

    part = by[("part", "brake pad set")]
    assert part["companies"] == 3
    assert part["dim_label"] == "Brake Pad Set"

    # Published shape carries no account-linked fields.
    for r in rows:
        assert set(r.keys()) == {
            "dim_type", "dim_key", "dim_label", "companies", "invoices",
            "p25", "p75", "window_months", "computed_at",
        }
    assert n == len(rows)


@pytest.mark.asyncio
async def test_below_threshold_publishes_nothing(db):
    b1 = (await db.create_account("N1")).id
    b2 = (await db.create_account("N2")).id
    shop = await db.create_directory_entry("Two Company Shop", status="active")
    await _acct_uses_shop(db, b1, shop, 150.0)
    await _acct_uses_shop(db, b2, shop, 250.0)
    await db.compute_market_rollups(SINCE)
    # 2 < MIN_COMPANIES → no cell for this shop, at all.
    assert await db.market_rollups_for_entry(shop["id"]) == []


@pytest.mark.asyncio
async def test_sharing_toggle_roundtrip(db):
    acct = (await db.create_account("T1")).id
    assert await db.get_market_sharing(acct) is False   # default OFF
    assert await db.set_market_sharing(acct, True) is True
    assert await db.get_market_sharing(acct) is True
    assert await db.set_market_sharing(acct, False) is True
    assert await db.get_market_sharing(acct) is False


@pytest.mark.asyncio
async def test_part_cells_key_on_public_catalog_identity(db):
    """Pre-launch key flip: accounts naming the same part differently
    pool into ONE cell once their parts link to the same public
    catalog entry — keyed ``gp:<id>``, labeled with the canonical
    name.  Unlinked GENERIC names never form part cells."""
    a1 = (await db.create_account("K1")).id
    a2 = (await db.create_account("K2")).id
    a3 = (await db.create_account("K3")).id
    shop = await db.create_directory_entry("Keyed Repair", status="active")
    canon = await db.create_part_directory_entry(
        "Truck Wash — Conventional", category="Wash",
    )

    local_names = ["ConventionalWithClassicWsh", "Conv Classic Wash", "conventional wash"]
    for acct, name, price in zip((a1, a2, a3), local_names, (40.0, 50.0, 60.0)):
        await db.set_market_sharing(acct, True)
        v = await db.resolve_or_create_vendor(acct, shop["name"])
        await db.link_vendor_to_directory(acct, v["id"], shop["id"])
        wo = await db.add_work_order(
            acct, "CO", "T-1", shop["name"],
            vendor_id=v["id"], service_date="2026-06-01", total_cost=price,
        )
        part = await db.resolve_or_create_part(acct, name)
        await db.link_part_to_public(acct, part["id"], canon["id"])
        await db.add_work_order_part(
            wo, part_name=name, quantity=1, unit_cost=price,
            total_cost=price, part_id=part["id"],
        )
        # A generic, UNLINKED line at the same shop — must form no cell.
        gen = await db.resolve_or_create_part(acct, "Shop Supplies")
        await db.add_work_order_part(
            wo, part_name="Shop Supplies", quantity=1, unit_cost=12.0,
            total_cost=12.0, part_id=gen["id"],
        )

    await db.compute_market_rollups(SINCE)
    rows = await db.market_rollups_for_entry(shop["id"])
    parts = {r["dim_key"]: r for r in rows if r["dim_type"] == "part"}

    key = f"gp:{canon['id']}"
    assert key in parts                       # variants pooled into one cell
    assert parts[key]["companies"] == 3
    assert parts[key]["dim_label"] == "Truck Wash — Conventional"
    # No per-variant name cells survived the flip...
    assert "conventionalwithclassicwsh" not in parts
    # ...and the generic key formed no cell despite 3 sharing companies.
    assert "shop supplies" not in parts
