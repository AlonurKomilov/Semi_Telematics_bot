"""Vendors API permission gates (Phase A).

Pins the manager-only access model: ``can_work_orders_all`` for every
endpoint, reads included — a vendor profile aggregates ALL trucks'
work orders + account-wide spend, so vehicle-scoped drivers (who see
only their own truck's WOs on the WO endpoints) must get 403 here,
never a filtered view that could silently widen later.
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
    acct = await database.create_account("Vendor Co")
    await database.add_company(acct.id, "VC", "key_vc", "Vendor Co")
    owner = await database.create_user(8101, acct.id, role=Role.OWNER)
    fleet = await database.create_user(8102, acct.id, role=Role.FLEET)
    driver = await database.create_user(8103, acct.id, role=Role.DRIVER)

    v = await database.resolve_or_create_vendor(acct.id, "Gate Test Repair", phone="555-9")
    await database.add_work_order(
        acct.id, "VC", "T-1", "Gate Test Repair",
        vendor_id=v["id"], service_date="2026-07-01", total_cost=100,
    )

    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = database
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": database, "acct": acct, "vendor": v,
        "token_owner": create_jwt(owner.telegram_id, acct.id, "owner"),
        "token_fleet": create_jwt(fleet.telegram_id, acct.id, "fleet"),
        "token_driver": create_jwt(driver.telegram_id, acct.id, "driver"),
    }
    _cp._db = _old_db


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_fleet_manager_full_access(seeded):
    """Fleet role (the default owner of this feature) can list, read
    profiles, create, and merge."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/vendors", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        assert any(v["name"] == "Gate Test Repair" for v in r.json()["vendors"])

        vid = seeded["vendor"]["id"]
        r = await c.get(f"/api/vendors/{vid}", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        body = r.json()
        assert body["vendor"]["id"] == vid
        assert len(body["work_orders"]) == 1

        r = await c.post("/api/vendors", headers=_h(seeded["token_fleet"]),
                         json={"name": "Second Shop"})
        assert r.status_code == 200
        second = r.json()

        r = await c.post(
            f"/api/vendors/{second['id']}/merge-into/{vid}",
            headers=_h(seeded["token_fleet"]),
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_driver_gets_403_everywhere(seeded):
    """Vehicle-scope WO access must NOT open the vendors surface —
    the profile aggregates other trucks' records."""
    transport = ASGITransport(app=seeded["app"])
    vid = seeded["vendor"]["id"]
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        for method, url, kw in (
            ("GET", "/api/vendors", {}),
            ("GET", f"/api/vendors/{vid}", {}),
            ("POST", "/api/vendors", {"json": {"name": "Nope"}}),
            ("PUT", f"/api/vendors/{vid}", {"json": {"phone": "1"}}),
            ("POST", f"/api/vendors/{vid}/merge-into/999", {}),
        ):
            r = await c.request(method, url, headers=_h(seeded["token_driver"]), **kw)
            assert r.status_code == 403, f"{method} {url} -> {r.status_code}"


@pytest.mark.asyncio
async def test_owner_can_read(seeded):
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/vendors", headers=_h(seeded["token_owner"]))
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_auto_pipeline_contributes_on_address_complete(seeded):
    """The directory pipeline is fully automatic: a name-only vendor
    contributes nothing (nothing to verify), and the moment the user
    completes the address via the ordinary edit PUT, the identity flows
    to the operator review queue — no manual suggest endpoint exists."""
    transport = ASGITransport(app=seeded["app"])
    db = seeded["db"]
    vid = seeded["vendor"]["id"]
    nkey = seeded["vendor"]["name_key"]

    async def queue_row():
        cur = await db._db.execute(
            "SELECT status, suggested_by_account FROM vendor_directory "
            "WHERE name_key = ?", (nkey,),
        )
        return await cur.fetchone()

    assert await queue_row() is None  # name-only → not contributed

    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.put(f"/api/vendors/{vid}", headers=_h(seeded["token_fleet"]),
                        json={"address": "42 Gate Rd, Springfield, IL"})
        assert r.status_code == 200

        # The manual suggest/link endpoints are gone by design.
        r = await c.post(f"/api/vendors/{vid}/suggest-to-directory",
                         headers=_h(seeded["token_fleet"]))
        assert r.status_code in (404, 405)

    row = await queue_row()
    assert row is not None
    assert dict(row)["status"] == "pending"
    assert dict(row)["suggested_by_account"] == seeded["acct"].id


@pytest.mark.asyncio
async def test_merge_dialog_directory_branch_links_and_enriches(seeded):
    """The dedup carve-out: the merge dialog's Directory branch links
    the vendor to a public entry (non-destructive — row + WOs stay),
    fills EMPTY contact fields only, and Unlink reverses the link."""
    transport = ASGITransport(app=seeded["app"])
    db = seeded["db"]
    acct = seeded["acct"]
    vid = seeded["vendor"]["id"]
    entry = await db.create_directory_entry(
        "Dedup Truck Stop", address="1 Interstate Way", phone="555-1",
        status="active",
    )
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post(f"/api/vendors/{vid}/link-directory/999999",
                         headers=_h(seeded["token_fleet"]))
        assert r.status_code == 404

        r = await c.post(f"/api/vendors/{vid}/link-directory/{entry['id']}",
                         headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200

        v = await db.get_vendor(vid, acct.id)
        assert v["global_vendor_id"] == entry["id"]
        assert v["address"] == "1 Interstate Way"   # empty field filled
        assert v["phone"] == "555-9"                # set field NEVER overwritten

        r = await c.delete(f"/api/vendors/{vid}/link-directory",
                           headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        v = await db.get_vendor(vid, acct.id)
        assert v["global_vendor_id"] is None

        r = await c.post(f"/api/vendors/{vid}/link-directory/{entry['id']}",
                         headers=_h(seeded["token_driver"]))
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_merge_carries_directory_link_to_survivor(seeded):
    """Merge is the dedup tool for correct reporting — the directory
    link must survive it.  If only the loser was linked, the winner
    inherits the link; an existing winner link is never overwritten."""
    db = seeded["db"]
    acct = seeded["acct"]
    entry = await db.create_directory_entry(
        "Merge Link Shop", address="1 Survivor Way", status="active",
    )
    loser = await db.resolve_or_create_vendor(acct.id, "Merge Link Shop TYPO")
    winner = await db.resolve_or_create_vendor(acct.id, "Merge Link Shop Real")
    await db.link_vendor_to_directory(acct.id, loser["id"], entry["id"])

    assert await db.merge_vendors(acct.id, loser["id"], winner["id"])
    survivor = await db.get_vendor(winner["id"], acct.id)
    assert survivor["global_vendor_id"] == entry["id"]

    # A winner that is already linked keeps its own link.
    other = await db.create_directory_entry(
        "Merge Other Shop", address="2 Keeper Rd", status="active",
    )
    loser2 = await db.resolve_or_create_vendor(acct.id, "Second Typo Shop")
    await db.link_vendor_to_directory(acct.id, loser2["id"], other["id"])
    assert await db.merge_vendors(acct.id, loser2["id"], winner["id"])
    survivor = await db.get_vendor(winner["id"], acct.id)
    assert survivor["global_vendor_id"] == entry["id"]


@pytest.mark.asyncio
async def test_directory_browse_endpoint(seeded):
    """Browse lists active entries with the caller's link flag; drivers
    stay locked out like the rest of the feature."""
    transport = ASGITransport(app=seeded["app"])
    db = seeded["db"]
    acct = seeded["acct"]
    e = await db.create_directory_entry(
        "Browse API Shop", address="9 Public Way", status="active",
    )
    await db.link_vendor_to_directory(acct.id, seeded["vendor"]["id"], e["id"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/vendors/directory/browse",
                        headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        mine = [x for x in r.json()["entries"] if x["id"] == e["id"]]
        assert len(mine) == 1
        assert mine[0]["linked_vendor_id"] == seeded["vendor"]["id"]
        assert "suggested_by_account" not in mine[0]

        r = await c.get("/api/vendors/directory/browse",
                        headers=_h(seeded["token_driver"]))
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_wo_create_passes_vendor_email_to_registry(seeded):
    """vendor_email on the WO form is registry-only: it enriches the
    vendor record and never breaks the WO insert (no email column on
    work_orders)."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/work-orders", headers=_h(seeded["token_fleet"]),
                         json={
                             "vehicle_name": "T-1",
                             "vendor_name": "Email Capture Shop",
                             "vendor_address": "12 Mail Rd",
                             "vendor_phone": "555-77",
                             "vendor_email": "shop@mail.test",
                         })
        assert r.status_code == 200, r.text
        vendors = (await c.get("/api/vendors",
                               headers=_h(seeded["token_fleet"]))).json()["vendors"]
        v = [x for x in vendors if x["name"] == "Email Capture Shop"][0]
        assert v["email"] == "shop@mail.test"
        assert v["address"] == "12 Mail Rd"


@pytest.mark.asyncio
async def test_identity_sharing_endpoints_and_private_status(seeded, monkeypatch):
    """PUT is owner-only (can_manage_account); OFF surfaces as the
    'private' banner state on unlinked vendors."""
    transport = ASGITransport(app=seeded["app"])
    vid = seeded["vendor"]["id"]
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/vendors/identity-sharing", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200 and r.json()["enabled"] is True

        # Fleet manager cannot flip account-level consent.
        r = await c.put("/api/vendors/identity-sharing",
                        headers=_h(seeded["token_fleet"]), json={"enabled": False})
        assert r.status_code == 403

        r = await c.put("/api/vendors/identity-sharing",
                        headers=_h(seeded["token_owner"]), json={"enabled": False})
        assert r.status_code == 200

        r = await c.get(f"/api/vendors/{vid}", headers=_h(seeded["token_fleet"]))
        assert r.json()["directory_status"] == "private"

        # Restore ON; with an address present the state becomes pending.
        await c.put("/api/vendors/identity-sharing",
                    headers=_h(seeded["token_owner"]), json={"enabled": True})
        await c.put(f"/api/vendors/{vid}", headers=_h(seeded["token_fleet"]),
                    json={"address": "77 Consent St"})
        r = await c.get(f"/api/vendors/{vid}", headers=_h(seeded["token_fleet"]))
        assert r.json()["directory_status"] in ("pending", "linked")


@pytest.mark.asyncio
async def test_market_not_sharing_exposes_count_only(seeded, monkeypatch):
    """Give-to-get pitch shows the REAL number of unlockable ranges —
    and provably no price values — before consent."""
    monkeypatch.setenv("MARKET_INTEL_ENABLED", "1")
    db = seeded["db"]
    acct = seeded["acct"]
    e = await db.create_directory_entry(
        "Count Shop", address="9 Range Rd", status="active",
    )
    # Two rollup cells exist for this entry (written directly — the
    # nightly job's output shape).
    now = "2026-07-16T00:00:00"
    for dim in ("brakes", "oil"):
        await db._db.execute(
            "INSERT INTO market_price_rollups (entry_id, dim_type, dim_key, "
            " dim_label, companies, invoices, p25, p75, window_months, computed_at) "
            "VALUES (?, 'service_task', ?, '', 3, 9, 100.0, 200.0, 12, ?)",
            (e["id"], dim, now),
        )
    await db._db.commit()
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get(f"/api/vendors/directory/{e['id']}/market",
                        headers=_h(seeded["token_fleet"]))
        body = r.json()
        assert body["reason"] == "not_sharing"
        assert body["available_count"] == 2
        assert body["rows"] == []
        assert "p25" not in str(body)


@pytest.mark.asyncio
async def test_vehicle_history_includes_work_orders_manager_only(seeded):
    """Tier-2 B2 (lives here for the app harness): completed shop
    invoices join /maintenance/history/{vehicle}; drafts stay out;
    drivers never receive the money-bearing work_orders array."""
    db = seeded["db"]
    acct = seeded["acct"]
    await db.add_work_order(
        acct.id, "VC", "T-1", "History Shop",
        service_date="2026-07-03", total_cost=250.0, status="completed",
    )
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/maintenance/history/T-1",
                        headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200, r.text
        body = r.json()
        wos = body.get("work_orders") or []
        mine = [w for w in wos if w["vendor_name"] == "History Shop"]
        assert len(mine) == 1
        assert mine[0]["total_cost"] == 250.0
        # The seeded DRAFT work order stays out of history.
        assert all(w["vendor_name"] != "Gate Test Repair" for w in wos)

        # Driver: no assigned trucks → safe-deny 404 (and never money).
        r = await c.get("/api/maintenance/history/T-1",
                        headers=_h(seeded["token_driver"]))
        assert r.status_code in (403, 404)


@pytest.mark.asyncio
async def test_status_paid_rejected_by_api(seeded):
    """Post-migration-154 contract: status is lifecycle-only — the API
    rejects the legacy status='paid' on create AND update (clean 422
    break per advisor decision; money state goes in payment_status)."""
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/work-orders", headers=_h(seeded["token_fleet"]),
                         json={"vehicle_name": "T-1", "status": "paid"})
        assert r.status_code == 422
        vid = seeded["vendor"]["id"]
        _ = vid
        # Legacy alias: a stale dashboard bundle POSTing the old
        # 'submitted' is accepted for one release and NORMALIZED to the
        # Fleetio 'closed' — never stored as the dead value.
        r = await c.post("/api/work-orders", headers=_h(seeded["token_fleet"]),
                         json={"vehicle_name": "T-1", "status": "submitted",
                               "payment_status": "paid"})
        assert r.status_code == 200
        wo_id = r.json()["id"]
        stored = await seeded["db"].get_work_order(wo_id, seeded["acct"].id)
        assert stored["status"] == "completed"
        r = await c.put(f"/api/work-orders/{wo_id}",
                        headers=_h(seeded["token_fleet"]),
                        json={"status": "paid"})
        assert r.status_code == 422
