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

    async def test_intake_link_flow(self, api):
        """Manager mints a fill-link → the carrier reads a sheet WITHOUT the
        recruiter-only section, submits their answers → content updates,
        recruiter-only survives untouched, the review flag raises — and a
        manager save clears it."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810040, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        sample = dict(_SAMPLE)
        sample["content"] = {
            **_SAMPLE["content"],
            "recruiter_only": [{"label": "Application Ownership", "value": "Exclusive 30 days"}],
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=sample)).json()["id"]

            r = await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                             json={"expires_in_days": 30})
            assert r.status_code == 200, r.text
            token = r.json()["token"]
            assert f"/carrier/{token}" in r.json()["url"]

            # Public prefill — NO auth header.  Internal section stripped.
            pub = await c.get(f"/api/carrier-directory/intake?token={token}")
            assert pub.status_code == 200, pub.text
            carrier = pub.json()["carrier"]
            assert carrier["name"] == sample["name"]
            assert "recruiter_only" not in carrier["content"]
            assert carrier["content"]["prequal"][0]["value"] == "At least 23 years of age"

            # Carrier submits — recruiter_only in the payload is IGNORED.
            sub = await c.post("/api/carrier-directory/intake", json={
                "token": token,
                "website": "https://apt.example.com",
                "experience_summary": "1 year OTR",
                "content": {
                    "application_process": "Email packets to recruiting@apt.example.com",
                    "prequal": [{"label": "Minimum Age", "value": "21"}],
                    "presentation": [{"label": "Sign-On Bonus", "value": "$1,000"}],
                    "recruiter_only": [{"label": "Application Ownership", "value": "HACKED"}],
                },
            })
            assert sub.status_code == 200, sub.text

            got = (await c.get(f"{BASE}/{cid}", headers=h)).json()
            assert got["website"] == "https://apt.example.com"
            assert got["content"]["prequal"][0]["value"] == "21"
            assert got["content"]["presentation"][0]["value"] == "$1,000"
            # The stored internal section survived; the injected one didn't land.
            assert got["content"]["recruiter_only"][0]["value"] == "Exclusive 30 days"
            assert bool(got["intake_review_pending"])
            assert got["intake_token"] == token  # managers see the credential

            # Manager save (even a no-op field patch) clears the review flag.
            up = await c.patch(f"{BASE}/{cid}", headers=h, json={"name": sample["name"]})
            assert up.status_code == 200
            assert not bool(up.json()["intake_review_pending"])

    async def test_intake_token_hidden_from_plain_recruiters(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810050, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810051, acct.id, role=Role.RECRUITER)
        hm = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=hm, json=_SAMPLE)).json()["id"]
            await c.post(f"{BASE}/{cid}/intake-link", headers=hm, json={})
            # Plain recruiter: can read the profile but never the token,
            # and cannot mint or revoke links.
            got = (await c.get(f"{BASE}/{cid}", headers=hr)).json()
            assert "intake_token" not in got
            assert (await c.post(f"{BASE}/{cid}/intake-link", headers=hr, json={})).status_code == 403
            assert (await c.delete(f"{BASE}/{cid}/intake-link", headers=hr)).status_code == 403

    async def test_intake_revoked_expired_unknown_all_404(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810060, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            token = (await c.post(f"{BASE}/{cid}/intake-link", headers=h, json={})).json()["token"]

            # Revoke → the public form 404s (uniform, no oracle).
            await c.delete(f"{BASE}/{cid}/intake-link", headers=h)
            assert (await c.get(f"/api/carrier-directory/intake?token={token}")).status_code == 404
            assert (await c.post("/api/carrier-directory/intake",
                                 json={"token": token, "content": {}})).status_code == 404

            # Expired → 404 too.
            await db.set_carrier_intake(
                acct.id, cid, token="expired-tok",
                expires_at="2020-01-01T00:00:00+00:00",
            )
            assert (await c.get("/api/carrier-directory/intake?token=expired-tok")).status_code == 404

            # Unknown / blank tokens.
            assert (await c.get("/api/carrier-directory/intake?token=nope")).status_code == 404
            assert (await c.get("/api/carrier-directory/intake?token=")).status_code == 404

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
