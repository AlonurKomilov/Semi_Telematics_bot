"""Parts feature API — the feature-owned ``can_parts`` gate, the
drill-down analytics, and the graduation wire contract.

Access model pinned here:
  * ``can_parts`` (owner/admin/fleet) — full feature: list, drill-down,
    edit, merge.
  * ``can_work_orders_all`` WITHOUT ``can_parts`` (safety) — the LIST
    only (it feeds the work-order editor's autocomplete); analytics and
    writes stay 403.
  * vehicle-scoped drivers — 403 everywhere (a part profile aggregates
    the whole account's spend).

Wire compat: the pre-graduation URLs (/work-orders/parts-catalog…) are
deprecated aliases of the SAME handlers — alias==primary is pinned.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db):
    database = pg_db
    acct = await database.create_account("Parts Co")
    await database.add_company(acct.id, "PC", "key_pc", "Parts Co")
    owner = await database.create_user(8201, acct.id, role=Role.OWNER)
    fleet = await database.create_user(8202, acct.id, role=Role.FLEET)
    safety = await database.create_user(8203, acct.id, role=Role.SAFETY)
    driver = await database.create_user(8204, acct.id, role=Role.DRIVER)

    shop_a = await database.resolve_or_create_vendor(acct.id, "Parts Shop Alpha")
    shop_b = await database.resolve_or_create_vendor(acct.id, "Parts Shop Beta")
    part = await database.resolve_or_create_part(acct.id, "Brake Pad Set")

    async def wo(vehicle, vendor, date, *, status="closed",
                 payment_status="unpaid", qty=1.0, unit=0.0, total=0.0):
        wo_id = await database.add_work_order(
            acct.id, "PC", vehicle, vendor["name"], vendor_id=vendor["id"],
            service_date=date, status=status, payment_status=payment_status,
            total_cost=total,
        )
        await database.add_work_order_part(
            wo_id, part_name=part["name"], quantity=qty, unit_cost=unit,
            total_cost=total, part_id=part["id"],
        )
        return wo_id

    # T-1 visits Shop Alpha (Jan) then Shop Beta (Mar): a 60-day gap.
    await wo("T-1", shop_a, "2026-01-10", qty=2, unit=50, total=100)
    await wo("T-1", shop_b, "2026-03-11", qty=2, unit=55, total=110)
    # T-2 buys once at Shop Alpha.
    await wo("T-2", shop_a, "2026-05-01", qty=1, unit=52, total=52)
    # Void invoices never count toward analytics — one of each flavour.
    await wo("T-1", shop_a, "2026-06-01", status="void", qty=10, unit=50, total=500)
    await wo("T-1", shop_a, "2026-06-02", payment_status="void",
             qty=10, unit=50, total=500)

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct, "part": part,
        "shop_a": shop_a, "shop_b": shop_b,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_fleet": create_jwt(fleet.telegram_id, acct.id, "fleet"),
        "token_safety": create_jwt(safety.telegram_id, acct.id, "safety"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }
    _cp._db = _old_db


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_fleet_drilldown_analytics_exclude_void(seeded):
    """The drill-down aggregates: recurrence per vehicle (with the
    mean-gap number), price per vendor, purchase history — and the two
    void invoices poison none of it."""
    transport = ASGITransport(app=seeded["app"])
    pid = seeded["part"]["id"]
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/parts", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        names = [p["name"] for p in r.json()["parts"]]
        assert "Brake Pad Set" in names

        r = await c.get(f"/api/parts/{pid}", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        data = r.json()
        assert data["part"]["id"] == pid

        veh = {v["vehicle_name"]: v for v in data["by_vehicle"]}
        assert set(veh) == {"T-1", "T-2"}
        assert veh["T-1"]["usage_count"] == 2          # void lines excluded
        assert veh["T-1"]["total_quantity"] == 4
        assert veh["T-1"]["avg_interval_days"] == 60.0  # Jan 10 → Mar 11
        assert veh["T-2"]["usage_count"] == 1
        assert veh["T-2"]["avg_interval_days"] is None  # single visit

        ven = {v["vendor_name"]: v for v in data["by_vendor"]}
        assert set(ven) == {"Parts Shop Alpha", "Parts Shop Beta"}
        assert ven["Parts Shop Alpha"]["purchases"] == 2   # voids excluded
        assert ven["Parts Shop Alpha"]["min_unit_price"] == 50.0
        assert ven["Parts Shop Alpha"]["max_unit_price"] == 52.0
        assert ven["Parts Shop Beta"]["avg_unit_price"] == 55.0

        assert len(data["purchases"]) == 3
        assert all(p["effective_unit_price"] is not None
                   for p in data["purchases"])


@pytest.mark.asyncio
async def test_gate_split_by_role(seeded):
    """safety = WO manager without can_parts: autocomplete list works,
    analytics/writes don't.  Drivers: 403 everywhere."""
    transport = ASGITransport(app=seeded["app"])
    pid = seeded["part"]["id"]
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/parts", headers=_h(seeded["token_safety"]))
        assert r.status_code == 200
        r = await c.get(f"/api/parts/{pid}", headers=_h(seeded["token_safety"]))
        assert r.status_code == 403
        r = await c.put(f"/api/parts/{pid}", headers=_h(seeded["token_safety"]),
                        json={"notes": "nope"})
        assert r.status_code == 403

        for method, url in (
            ("GET", "/api/parts"),
            ("GET", f"/api/parts/{pid}"),
            ("PUT", f"/api/parts/{pid}"),
            ("POST", f"/api/parts/{pid}/merge-into/{pid + 1}"),
        ):
            r = await c.request(method, url, headers=_h(seeded["token_driver"]),
                                json={"notes": "x"} if method == "PUT" else None)
            assert r.status_code == 403, (method, url)


@pytest.mark.asyncio
async def test_update_and_rename_collision(seeded):
    transport = ASGITransport(app=seeded["app"])
    db = seeded["db"]
    acct = seeded["acct"]
    other = await db.resolve_or_create_part(acct.id, "Oil Filter")
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.put(f"/api/parts/{other['id']}",
                        headers=_h(seeded["token_fleet"]),
                        json={"part_number": "OF-100", "notes": "OEM only"})
        assert r.status_code == 200

        # Renaming onto an existing part's key → 409, merge is the fix.
        r = await c.put(f"/api/parts/{other['id']}",
                        headers=_h(seeded["token_fleet"]),
                        json={"name": "brake  pad  SET"})
        assert r.status_code == 409

        r = await c.put(f"/api/parts/{other['id']}",
                        headers=_h(seeded["token_fleet"]),
                        json={"name": "Oil Filter Premium"})
        assert r.status_code == 200
        fresh = await db.get_catalog_part(other["id"], acct.id)
        assert fresh["name"] == "Oil Filter Premium"
        assert fresh["part_number"] == "OF-100"


@pytest.mark.asyncio
async def test_merge_repoints_lines_and_aliases(seeded):
    """Same contract as vendor merge: lines repoint, the loser's key
    becomes an alias (re-syncs resolve to the survivor)."""
    transport = ASGITransport(app=seeded["app"])
    db = seeded["db"]
    acct = seeded["acct"]
    pid = seeded["part"]["id"]

    typo = await db.resolve_or_create_part(acct.id, "Brake Padd Set")
    wo_id = await db.add_work_order(
        acct.id, "PC", "T-2", seeded["shop_b"]["name"],
        vendor_id=seeded["shop_b"]["id"], service_date="2026-06-20",
        status="closed", total_cost=57,
    )
    await db.add_work_order_part(
        wo_id, part_name="Brake Padd Set", quantity=1, unit_cost=57,
        total_cost=57, part_id=typo["id"],
    )

    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(f"/api/parts/{typo['id']}/merge-into/{pid}",
                         headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200

        r = await c.get(f"/api/parts/{pid}", headers=_h(seeded["token_fleet"]))
        assert len(r.json()["purchases"]) == 4  # absorbed the typo's line

    resolved = await db.resolve_or_create_part(acct.id, "Brake Padd Set")
    assert resolved["id"] == pid  # alias survives


@pytest.mark.asyncio
async def test_add_part_resolve_semantics(seeded):
    """POST /parts is Add-vendor's contract: create when new, return
    the EXISTING row with created=false when the name already resolves
    — typed fields are never silently applied to the existing row."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/parts", headers=_h(seeded["token_fleet"]),
                         json={"name": "Air Dryer Cartridge",
                               "part_number": "AD-9", "notes": "OEM only"})
        assert r.status_code == 200
        body = r.json()
        assert body["created"] is True
        assert body["part"]["part_number"] == "AD-9"
        pid = body["part"]["id"]

        # Same normalized name → existing row, honest flag, no overwrite.
        r = await c.post("/api/parts", headers=_h(seeded["token_fleet"]),
                         json={"name": "air  dryer  CARTRIDGE",
                               "part_number": "SHOULD-NOT-APPLY"})
        assert r.status_code == 200
        body2 = r.json()
        assert body2["created"] is False
        assert body2["part"]["id"] == pid
        assert body2["part"]["part_number"] == "AD-9"

        r = await c.post("/api/parts", headers=_h(seeded["token_driver"]),
                         json={"name": "Nope"})
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_legacy_alias_equals_primary(seeded):
    """/work-orders/parts-catalog is a deprecated alias of /parts —
    identical payload, identical behavior (same handler objects)."""
    transport = ASGITransport(app=seeded["app"])
    pid = seeded["part"]["id"]
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        primary = await c.get("/api/parts", headers=_h(seeded["token_fleet"]))
        legacy = await c.get("/api/work-orders/parts-catalog",
                             headers=_h(seeded["token_fleet"]))
        assert primary.status_code == legacy.status_code == 200
        assert primary.json() == legacy.json()

        for base in (f"/api/parts/{pid}",
                     f"/api/work-orders/parts-catalog/{pid}"):
            r = await c.post(f"{base}/merge-into/{pid}",
                             headers=_h(seeded["token_fleet"]))
            assert r.status_code == 422  # self-merge rejected on both
