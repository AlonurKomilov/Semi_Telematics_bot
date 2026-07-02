"""Tests for the Carrier Knowledge Base (recruiter carrier directory).

Focus: the read/edit permission split — a ``recruiter`` reads, a recruiter
MANAGER (``recruiter`` + ``is_manager``) edits — plus tenant isolation,
content JSON round-trip, and 404 handling.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/carrier_dir_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


def _headers(db_user, acct, role: str, is_manager: bool = False):
    from interfaces.api.auth import create_jwt
    token = create_jwt(
        db_user.telegram_id or 0, acct.id, role,
        user_id=db_user.id, is_manager=is_manager,
    )
    return {"Authorization": f"Bearer {token}"}


BASE = "/api/carrier-directory/carriers"

_SAMPLE = {
    "name": "American Power Trucking, LLC",
    "website": "https://americanpowertrucking.com/",
    "experience_summary": "2 years of verifiable TT OTR experience in the past 3 years",
    "content": {
        "prequal": [{"label": "Minimum Age", "value": "At least 23 years of age"}],
        "presentation": [{"label": "Sign-On Bonus", "value": "$500"}],
    },
}


class TestCarrierDirectory:
    async def test_manager_full_crud_and_content_round_trip(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810001, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post(BASE, headers=h, json=_SAMPLE)
            assert r.status_code == 200, r.text
            cid = r.json()["id"]
            assert r.json()["content"]["prequal"][0]["value"] == "At least 23 years of age"

            lst = await c.get(BASE, headers=h)
            assert lst.status_code == 200
            assert any(x["id"] == cid for x in lst.json()["items"])
            # The list view carries name + summary only — never the big content blob.
            assert "content" not in lst.json()["items"][0]

            got = await c.get(f"{BASE}/{cid}", headers=h)
            assert got.status_code == 200
            assert got.json()["content"]["presentation"][0]["value"] == "$500"

            up = await c.patch(f"{BASE}/{cid}", headers=h,
                               json={"experience_summary": "1 year OTR", "content": {"prequal": []}})
            assert up.status_code == 200
            assert up.json()["experience_summary"] == "1 year OTR"
            assert up.json()["content"]["prequal"] == []

            assert (await c.delete(f"{BASE}/{cid}", headers=h)).status_code == 200
            assert (await c.get(f"{BASE}/{cid}", headers=h)).status_code == 404

    async def test_recruiter_is_read_only(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810010, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810011, acct.id, role=Role.RECRUITER)
        hm = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=hm, json=_SAMPLE)).json()["id"]
            # recruiter CAN read
            assert (await c.get(BASE, headers=hr)).status_code == 200
            assert (await c.get(f"{BASE}/{cid}", headers=hr)).status_code == 200
            # recruiter CANNOT write (edit needs the manager tier: is_manager)
            assert (await c.post(BASE, headers=hr, json=_SAMPLE)).status_code == 403
            assert (await c.patch(f"{BASE}/{cid}", headers=hr, json={"name": "x"})).status_code == 403
            assert (await c.delete(f"{BASE}/{cid}", headers=hr)).status_code == 403

    async def test_driver_blocked_entirely(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        drv = await db.create_user(810020, acct.id, role=Role.DRIVER)
        hd = _headers(drv, acct, "driver")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get(BASE, headers=hd)).status_code == 403

    async def test_tenant_isolation(self, api):
        app, db = api
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        mgr_a = await db.create_user(810030, acct_a.id, role=Role.RECRUITER)
        mgr_b = await db.create_user(810031, acct_b.id, role=Role.RECRUITER)
        ha = _headers(mgr_a, acct_a, "recruiter", is_manager=True)
        hb = _headers(mgr_b, acct_b, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=ha, json=_SAMPLE)).json()["id"]
            # B's list never contains A's carrier
            assert all(x["id"] != cid for x in (await c.get(BASE, headers=hb)).json()["items"])
            # B can't fetch / edit / delete A's carrier — out of its scope → 404
            assert (await c.get(f"{BASE}/{cid}", headers=hb)).status_code == 404
            assert (await c.patch(f"{BASE}/{cid}", headers=hb, json={"name": "x"})).status_code == 404
            assert (await c.delete(f"{BASE}/{cid}", headers=hb)).status_code == 404
