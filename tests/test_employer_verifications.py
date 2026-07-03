"""Tests for §391.23 employer verification (safety-history investigation).

Covers the target derivation (FMCSA flag + 3-year window), the send flow
(mocked email; attempts trail), outcome updates, and account scoping.
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/verif_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role
from features.applications.service import verification_targets


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


JPG = b"\xFF\xD8\xFF\xE0" + b"\x00" * 40
PDF = b"%PDF-1.4" + b"\x00" * 40
_FILES = {
    "cdl_front": ("a.jpg", JPG, "image/jpeg"),
    "cdl_back": ("b.jpg", JPG, "image/jpeg"),
    "medical": ("m.pdf", PDF, "application/pdf"),
}

# Employment mix: [0] recent FMCSA (target), [1] ancient FMCSA (out of the
# 3-year window), [2] recent non-FMCSA (not regulated → not a target).
_EMPLOYMENT = [
    {"company": "Acme Transport", "fmcsa": "yes", "from": "2024-01", "to": "",
     "current": True, "city": "Austin", "state": "TX", "phone": "555-1000",
     "position": "Driver", "contactOk": "later"},
    {"company": "Old Haulers", "fmcsa": "yes", "from": "2015-01", "to": "2017-06",
     "position": "Driver", "contactOk": "yes"},
    {"company": "Office Job Inc", "fmcsa": "no", "from": "2023-01", "to": "2023-12"},
]


def _payload():
    return json.dumps({
        "personal": {"first": "Jane", "last": "Roe", "email": "jane@x.com",
                     "phone": "555-1212", "city": "Austin", "state": "TX",
                     "dob": "1992-05-05", "ssn": "111-22-3333"},
        "cdl": {"state": "TX", "class": "A", "number": "X123"},
        "experience": {"yearsCdl": "3"},
        "employment": _EMPLOYMENT,
        "consents": {"truthful": True, "psp": True, "mvr": True,
                     "clearinghouse": True, "fcra": True, "drug": True,
                     "employment_verification": True,
                     "sigMode": "type", "sigName": "Jane Roe", "sigDate": "2026-07-01"},
    })


def _headers(db_user, acct):
    from interfaces.api.auth import create_jwt
    return {"Authorization": f"Bearer {create_jwt(db_user.telegram_id or 0, acct.id, 'recruiter', user_id=db_user.id)}"}


async def _submitted_app(app, db, tag=900100):
    acct = await db.create_account(f"Verif Co {tag}")
    rec = await db.create_user(tag, acct.id, role=Role.RECRUITER)
    link = await db.create_application_link(acct.id, label="t")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/api/applications/apply",
                         data={"link_token": link["token"], "application": _payload()},
                         files=_FILES)
        assert r.status_code == 200, r.text
        app_id = r.json()["application_id"]
    return acct, rec, app_id


class TestTargetDerivation:
    def test_fmcsa_flag_and_three_year_window(self):
        targets = verification_targets({"employment": _EMPLOYMENT})
        assert [t["company"] for t in targets] == ["Acme Transport"]
        assert targets[0]["employer_index"] == 0
        assert targets[0]["contact_ok"] == "later"

    def test_unparseable_end_date_kept(self):
        targets = verification_targets({"employment": [
            {"company": "Mystery", "fmcsa": "yes", "from": "2020-01", "to": "unknown"}]})
        assert len(targets) == 1   # over-include is the safe failure


class TestVerificationFlow:
    async def test_list_send_resend_and_outcome(self, api, monkeypatch):
        app, db = api
        acct, rec, app_id = await _submitted_app(app, db, 900101)
        h = _headers(rec, acct)

        sent: list[dict] = []
        def fake_send(**kw):
            sent.append(kw)
            return True
        monkeypatch.setattr(
            "capabilities.email.application_emails.send_verification_request_email", fake_send)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Derived list: only the qualifying employer, nothing engaged yet.
            r = await c.get(f"/api/applications/{app_id}/verifications", headers=h)
            assert r.status_code == 200
            items = r.json()["items"]
            assert len(items) == 1 and items[0]["verification"] is None

            # Send → row recorded, attempt 1, PDF attached to the email.
            r = await c.post(f"/api/applications/{app_id}/verifications/send", headers=h,
                             json={"employer_index": 0, "email": "safety@acme.com"})
            assert r.status_code == 200, r.text
            v = r.json()["verification"]
            assert v["status"] == "sent" and v["attempts"] == 1
            assert sent[0]["pdf_bytes"][:4] == b"%PDF"
            assert sent[0]["to"] == "safety@acme.com"

            # Re-send → same row, attempts 2 (the good-faith trail).
            r = await c.post(f"/api/applications/{app_id}/verifications/send", headers=h,
                             json={"employer_index": 0, "email": "safety@acme.com"})
            assert r.json()["verification"]["attempts"] == 2

            # Outcome: mark received → status + responded_at.
            vid = r.json()["verification"]["id"]
            r = await c.patch(f"/api/applications/{app_id}/verifications/{vid}", headers=h,
                              json={"status": "received", "notes": "clean record"})
            assert r.status_code == 200
            r = await c.get(f"/api/applications/{app_id}/verifications", headers=h)
            got = r.json()["items"][0]["verification"]
            assert got["status"] == "received" and got["responded_at"] and got["notes"] == "clean record"

            # Non-target employer index refuses.
            r = await c.post(f"/api/applications/{app_id}/verifications/send", headers=h,
                             json={"employer_index": 2, "email": "x@y.com"})
            assert r.status_code == 422

            # The DQ packet renders with the investigation trail included.
            pkt = await c.get(f"/api/applications/{app_id}/packet.pdf", headers=h)
            assert pkt.status_code == 200 and pkt.content[:4] == b"%PDF"

    async def test_account_scoping(self, api, monkeypatch):
        app, db = api
        _acct_a, _rec_a, app_id = await _submitted_app(app, db, 900102)
        acct_b = await db.create_account("Other Co")
        rec_b = await db.create_user(900103, acct_b.id, role=Role.RECRUITER)
        hb = _headers(rec_b, acct_b)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.get(f"/api/applications/{app_id}/verifications", headers=hb)).status_code == 404
            assert (await c.post(f"/api/applications/{app_id}/verifications/send", headers=hb,
                                 json={"employer_index": 0, "email": "x@y.com"})).status_code == 404
