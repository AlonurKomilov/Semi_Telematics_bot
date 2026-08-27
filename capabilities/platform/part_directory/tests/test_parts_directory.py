"""Public parts catalog — operator curation + candidates queue +
adopt fan-out + the account-side browse/link surface.

Pins the advisor guardrails:
  * generic-key blocklist (creation, candidates, adoption),
  * dismissal tombstones (candidates queue stays workable),
  * alias key never equals an entry key,
  * unlink SUPPRESSION (adopt re-fires must honor a user's unlink),
  * fill-empty part_number only (user vocabulary untouched),
  * resolve-time autolink for parts created after curation.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def seeded(pg_db, monkeypatch):
    db = pg_db
    acct_a = await db.create_account("Parts Pub Co A")
    acct_b = await db.create_account("Parts Pub Co B")
    op = await db.create_user(910001, acct_a.id, role=Role.OWNER)
    fleet_a = await db.create_user(910002, acct_a.id, role=Role.FLEET)
    driver_a = await db.create_user(910003, acct_a.id, role=Role.DRIVER)

    import capabilities.permissions.roles as perms
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {op.telegram_id})

    # The same messy synced name in BOTH accounts → a candidate.
    pa = await db.resolve_or_create_part(acct_a.id, "DDEA4711800209 DET OIL FILTER")
    pb = await db.resolve_or_create_part(acct_b.id, "ddea4711800209  det oil filter")
    # Single-account name → NOT a candidate.
    only_a = await db.resolve_or_create_part(acct_a.id, "Rare Widget 77")
    # Generic name in both accounts → NEVER a candidate.
    await db.resolve_or_create_part(acct_a.id, "Shop Supplies")
    await db.resolve_or_create_part(acct_b.id, "Shop Supplies")

    import infra.platform as _cp
    _old = _cp._db
    _cp._db = db
    from interfaces.api.app import create_api
    app = create_api()

    yield {
        "app": app, "db": db, "acct_a": acct_a, "acct_b": acct_b,
        "part_a": pa, "part_b": pb, "only_a": only_a,
        "token_op": create_jwt(op.telegram_id, acct_a.id, "owner"),
        "token_fleet": create_jwt(fleet_a.telegram_id, acct_a.id, "fleet"),
        "token_driver": create_jwt(driver_a.telegram_id, acct_a.id, "driver"),
    }
    _cp._db = _old


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_operator_create_blocklist_and_duplicates(seeded):
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/system/parts-directory", headers=_h(seeded["token_op"]),
                         json={"name": "Fuel Filter — Detroit DD15",
                               "category": "Filters", "part_number": "DDEA4711800209"})
        assert r.status_code == 200
        entry = r.json()
        assert entry["status"] == "active"

        # Generic names can never become canonical identities.
        r = await c.post("/api/system/parts-directory", headers=_h(seeded["token_op"]),
                         json={"name": "Labor"})
        assert r.status_code == 422
        assert "generic" in r.json()["detail"].lower()

        r = await c.post("/api/system/parts-directory", headers=_h(seeded["token_op"]),
                         json={"name": "fuel  filter — detroit  DD15"})
        assert r.status_code == 422  # key collision

        # Customer tokens never reach /system.
        r = await c.get("/api/system/parts-directory", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_candidates_queue_blocklist_and_dismiss(seeded):
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/system/parts-directory/candidates",
                        headers=_h(seeded["token_op"]))
        assert r.status_code == 200
        keys = {row["name_key"] for row in r.json()["candidates"]}
        assert "ddea4711800209 det oil filter" in keys   # 2 accounts share it
        assert "rare widget 77" not in keys              # single account
        assert "shop supplies" not in keys               # generic blocklist

        cand = next(row for row in r.json()["candidates"]
                    if row["name_key"] == "ddea4711800209 det oil filter")
        assert cand["account_count"] == 2

        r = await c.post("/api/system/parts-directory/candidates/dismiss",
                         headers=_h(seeded["token_op"]),
                         json={"raw_key": "DDEA4711800209 DET OIL FILTER"})
        assert r.status_code == 200
        r = await c.get("/api/system/parts-directory/candidates",
                        headers=_h(seeded["token_op"]))
        keys = {row["name_key"] for row in r.json()["candidates"]}
        assert "ddea4711800209 det oil filter" not in keys  # tombstoned


@pytest.mark.asyncio
async def test_promote_adopts_both_accounts_fill_empty_part_number(seeded):
    db = seeded["db"]
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/system/parts-directory/candidates/promote",
                         headers=_h(seeded["token_op"]),
                         json={"raw_key": "ddea4711800209 det oil filter",
                               "canonical_name": "Detroit DD15 Oil Filter",
                               "category": "Filters",
                               "part_number": "DDEA4711800209"})
        assert r.status_code == 200
        entry = r.json()
        assert entry["source"] == "promoted"

    a = await db.get_catalog_part(seeded["part_a"]["id"], seeded["acct_a"].id)
    b = await db.get_catalog_part(seeded["part_b"]["id"], seeded["acct_b"].id)
    assert a["global_part_id"] == entry["id"]   # adopted via alias
    assert b["global_part_id"] == entry["id"]
    assert a["part_number"] == "DDEA4711800209"  # fill-empty
    # User vocabulary untouched.
    assert a["name"] == "DDEA4711800209 DET OIL FILTER"

    # Resolve-time autolink: a THIRD usage created after curation links
    # on save (canonical name and alias both resolve).
    fresh = await db.resolve_or_create_part(seeded["acct_a"].id, "Detroit DD15 Oil Filter")
    assert fresh["global_part_id"] == entry["id"]


@pytest.mark.asyncio
async def test_unlink_suppression_survives_adopt_refire(seeded):
    db = seeded["db"]
    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/system/parts-directory/candidates/promote",
                         headers=_h(seeded["token_op"]),
                         json={"raw_key": "ddea4711800209 det oil filter",
                               "canonical_name": "Detroit DD15 Oil Filter"})
        entry = r.json()
        pid = seeded["part_a"]["id"]

        # Account A says: wrong match — unlink.
        r = await c.delete(f"/api/parts/{pid}/link-public",
                           headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        a = await db.get_catalog_part(pid, seeded["acct_a"].id)
        assert a["global_part_id"] is None
        assert a["public_link_suppressed"] is True

        # Operator work re-fires adoption — the user's call must stick.
        r = await c.post(f"/api/system/parts-directory/{entry['id']}/aliases",
                         headers=_h(seeded["token_op"]),
                         json={"name": "DET OIL FILTER 4711800209"})
        assert r.status_code == 200
        a = await db.get_catalog_part(pid, seeded["acct_a"].id)
        assert a["global_part_id"] is None      # still unlinked
        # ...while account B stays linked.
        b = await db.get_catalog_part(seeded["part_b"]["id"], seeded["acct_b"].id)
        assert b["global_part_id"] == entry["id"]

        # Explicit re-link clears suppression.
        r = await c.post(f"/api/parts/{pid}/link-public/{entry['id']}",
                         headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        a = await db.get_catalog_part(pid, seeded["acct_a"].id)
        assert a["global_part_id"] == entry["id"]
        assert a["public_link_suppressed"] is False


@pytest.mark.asyncio
async def test_alias_guardrails(seeded):
    db = seeded["db"]
    transport = ASGITransport(app=seeded["app"])
    entry = await db.create_part_directory_entry("Coolant Hose Upper")
    other = await db.create_part_directory_entry("Coolant Hose Lower")
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        # Alias key == another entry's key → ambiguous, rejected.
        r = await c.post(f"/api/system/parts-directory/{entry['id']}/aliases",
                         headers=_h(seeded["token_op"]),
                         json={"name": "coolant hose LOWER"})
        assert r.status_code == 422
        # Generic alias → rejected.
        r = await c.post(f"/api/system/parts-directory/{entry['id']}/aliases",
                         headers=_h(seeded["token_op"]),
                         json={"name": "Misc"})
        assert r.status_code == 422
        r = await c.post(f"/api/system/parts-directory/{entry['id']}/aliases",
                         headers=_h(seeded["token_op"]),
                         json={"name": "upper coolant hose"})
        assert r.status_code == 200
        assert other  # silence linters — both entries live


@pytest.mark.asyncio
async def test_browse_identity_only_with_link_state_and_gates(seeded):
    db = seeded["db"]
    transport = ASGITransport(app=seeded["app"])
    entry = await db.create_part_directory_entry(
        "Detroit DD15 Oil Filter", category="Filters",
    )
    # Alias matches both seeded accounts' rows → adoption linked A.
    await db.add_part_directory_alias(entry["id"], "DDEA4711800209 DET OIL FILTER")
    archived = await db.create_part_directory_entry("Old Obsolete Belt")
    await db.update_part_directory_entry(archived["id"], status="archived")

    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/parts/public/browse", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        rows = {e["name"]: e for e in r.json()["entries"]}
        assert "Detroit DD15 Oil Filter" in rows
        assert "Old Obsolete Belt" not in rows          # archived hidden
        mine = rows["Detroit DD15 Oil Filter"]
        assert mine["linked_part_id"] == seeded["part_a"]["id"]
        assert "usage" not in str(rows).lower()         # identity only

        r = await c.get("/api/parts/public/browse", headers=_h(seeded["token_driver"]))
        assert r.status_code == 403

        # Part detail carries the joined public identity (category via
        # join — never copied to the user row).
        r = await c.get(f"/api/parts/{seeded['part_a']['id']}",
                        headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        body = r.json()
        assert body["public"]["name"] == "Detroit DD15 Oil Filter"
        assert body["public"]["category"] == "Filters"
        assert body["part"].get("category") is None


@pytest.mark.asyncio
async def test_market_intel_console_switch(seeded):
    """The launch switch lives on the console now: operator flips it,
    every reader follows within the cache TTL (same-process: instantly)
    — no .env, no restart.  Customer tokens never reach /system."""
    from adapters.storage.platform_settings import _settings_cache
    _settings_cache.clear()   # cross-test cache hygiene

    transport = ASGITransport(app=seeded["app"])
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/system/market-intel", headers=_h(seeded["token_op"]))
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False and body["switch"] is False
        assert "sharing_accounts" in body and "part_geo_cells" in body

        r = await c.put("/api/system/market-intel", headers=_h(seeded["token_op"]),
                        json={"enabled": True})
        assert r.status_code == 200 and r.json()["enabled"] is True

        # Customer-side reader follows the switch (no env var set).
        r = await c.get("/api/vendors/market-sharing", headers=_h(seeded["token_fleet"]))
        assert r.status_code == 200
        assert r.json()["available"] is True

        # Preview trigger works regardless of the switch.
        r = await c.post("/api/system/market-intel/rollup-now",
                         headers=_h(seeded["token_op"]))
        assert r.status_code == 200

        r = await c.put("/api/system/market-intel", headers=_h(seeded["token_op"]),
                        json={"enabled": False})
        assert r.status_code == 200
        r = await c.get("/api/vendors/market-sharing", headers=_h(seeded["token_fleet"]))
        assert r.json()["available"] is False

        # Customer tokens never reach the cockpit.
        for method in ("GET", "PUT"):
            r = await c.request(method, "/api/system/market-intel",
                                headers=_h(seeded["token_fleet"]),
                                json={"enabled": True} if method == "PUT" else None)
            assert r.status_code == 403
