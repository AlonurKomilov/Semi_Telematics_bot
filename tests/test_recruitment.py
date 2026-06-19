"""Tests for the driver-recruiting intake feature.

Covers:
- infra.file_safety magic-byte validation (the public-upload guard)
- public POST /recruitment/apply: token gate, file-content validation,
  field validation, happy path
- recruiter endpoints: links + applications, permission-gated
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/recruit_test_store")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


# ── file_safety unit ────────────────────────────────────────────────


class TestFileSafety:
    def test_real_media_accepted(self):
        from infra.file_safety import validate_upload
        for magic in (b"\xFF\xD8\xFF\xE0", b"\x89PNG\r\n\x1a\n", b"%PDF-1.4"):
            ok, mime, _ = validate_upload(magic + b"\x00" * 40, max_bytes=10_000)
            assert ok, mime

    def test_disguised_executable_rejected(self):
        from infra.file_safety import validate_upload
        ok, _, reason = validate_upload(b"MZ\x90\x00" + b"\x00" * 40, max_bytes=10_000)
        assert not ok and reason == "unsupported_file_type"

    def test_svg_and_html_rejected(self):
        from infra.file_safety import validate_upload
        for payload in (b"<svg onload=alert(1)>", b"<!DOCTYPE html><script>"):
            ok, _, _ = validate_upload(payload + b"\x00" * 40, max_bytes=10_000)
            assert not ok

    def test_oversized_rejected(self):
        from infra.file_safety import validate_upload
        ok, _, reason = validate_upload(b"%PDF" + b"\x00" * 100, max_bytes=10)
        assert not ok and reason == "file_too_large"

    def test_empty_rejected(self):
        from infra.file_safety import validate_upload
        ok, _, reason = validate_upload(b"", max_bytes=10_000)
        assert not ok and reason == "empty_file"


# ── API fixtures ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    from interfaces.api.app import create_api
    app = create_api()
    return app, pg_db


def _app_payload(**over):
    base = {
        "personal": {"first": "Jane", "last": "Roe", "email": "jane@x.com",
                     "phone": "555-1212", "city": "Austin", "state": "TX",
                     "dob": "1992-05-05", "ssn": "111-22-3333"},
        "cdl": {"state": "TX", "class": "A"},
        "experience": {"yearsCdl": "3"},
        "employment": [{"company": "Acme"}],
        # A valid submission must affirm every legally load-bearing consent
        # (server-enforced in _validate_application), mirroring the form.
        "consents": {"truthful": True, "psp": True, "mvr": True,
                     "clearinghouse": True, "fcra": True, "drug": True,
                     "sigMode": "type", "sigName": "Jane Roe"},
    }
    base.update(over)
    return json.dumps(base)


JPG = b"\xFF\xD8\xFF\xE0" + b"\x00" * 40
PDF = b"%PDF-1.4" + b"\x00" * 40
EXE = b"MZ\x90\x00" + b"\x00" * 40

_GOOD_FILES = {
    "cdl_front": ("a.jpg", JPG, "image/jpeg"),
    "cdl_back": ("b.jpg", JPG, "image/jpeg"),
    "medical": ("m.pdf", PDF, "application/pdf"),
}


class TestPublicSubmit:
    async def test_invalid_token_404(self, api):
        app, _ = api
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": "BOGUS", "application": _app_payload()},
                             files=_GOOD_FILES)
        assert r.status_code == 404

    async def test_disguised_file_rejected(self, api):
        app, db = api
        acct = await db.create_account("Apply A")
        link = await db.create_recruitment_link(acct.id)
        files = dict(_GOOD_FILES); files["cdl_front"] = ("a.jpg", EXE, "image/jpeg")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=files)
        assert r.status_code == 422
        assert "rejected" in r.json()["detail"].lower()

    async def test_missing_required_doc_422(self, api):
        app, db = api
        acct = await db.create_account("Apply B")
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files={"cdl_front": _GOOD_FILES["cdl_front"]})
        assert r.status_code == 422

    async def test_bad_email_422(self, api):
        app, db = api
        acct = await db.create_account("Apply C")
        link = await db.create_recruitment_link(acct.id)
        payload = _app_payload(personal={"first": "J", "last": "R", "email": "nope"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": payload},
                             files=_GOOD_FILES)
        assert r.status_code == 422

    async def test_missing_certification_422(self, api):
        app, db = api
        acct = await db.create_account("Apply D")
        link = await db.create_recruitment_link(acct.id)
        payload = _app_payload(consents={"truthful": False, "sigMode": "type"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": payload},
                             files=_GOOD_FILES)
        assert r.status_code == 422

    async def test_happy_path_stores_application(self, api):
        app, db = api
        acct = await db.create_account("Apply E")
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
        assert r.status_code == 200, r.text
        assert r.json()["reference"].startswith("APP-")
        apps = await db.list_driver_applications(acct.id)
        assert len(apps) == 1 and apps[0]["first_name"] == "Jane"
        # PII encrypted at rest, decrypted only in detail
        detail = await db.get_driver_application(acct.id, apps[0]["id"])
        assert detail["ssn"] == "111-22-3333"

    async def test_fmcsa_fields_round_trip(self, api):
        """The 3 FMCSA MVP additions persist: 3-yr address history,
        per-job employment gap explanation, and the disclosure version."""
        app, db = api
        acct = await db.create_account("FMCSA Co")
        link = await db.create_recruitment_link(acct.id)
        payload = _app_payload(
            addressHistory=[{"addr1": "12 Old Rd", "city": "Reno", "state": "NV",
                             "from": "2022-01", "to": "2024-01"}],
            employment=[{"company": "Acme", "gapExplanation": "commercial school"}],
            disclosureVersion="2026-06-1",
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": payload},
                             files=_GOOD_FILES)
        assert r.status_code == 200, r.text
        apps = await db.list_driver_applications(acct.id)
        detail = await db.get_driver_application(acct.id, apps[0]["id"])
        assert detail["address_history"][0]["city"] == "Reno"
        assert detail["employment"][0]["gapExplanation"] == "commercial school"
        assert detail["disclosure_version"] == "2026-06-1"


# ── Recruiter endpoints (permission-gated) ──────────────────────────


def _recruiter_headers(db_user, acct):
    from interfaces.api.auth import create_jwt
    token = create_jwt(db_user.telegram_id or 0, acct.id, "recruiter", user_id=db_user.id)
    return {"Authorization": f"Bearer {token}"}


class TestRecruiterEndpoints:
    async def test_link_and_application_flow(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        recruiter = await db.create_user(556677, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(recruiter, acct)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # create link
            r = await c.post("/api/recruitment/links", headers=headers,
                             json={"label": "Indeed", "source": "indeed"})
            assert r.status_code == 200, r.text
            token = r.json()["token"]

            # public applicant submits through that link
            r2 = await c.post("/api/recruitment/apply",
                              data={"link_token": token, "application": _app_payload()},
                              files=_GOOD_FILES)
            assert r2.status_code == 200
            app_id = r2.json()["application_id"]

            # recruiter lists + reads detail
            r3 = await c.get("/api/recruitment/applications", headers=headers)
            assert r3.status_code == 200 and r3.json()["count"] == 1
            r4 = await c.get(f"/api/recruitment/applications/{app_id}", headers=headers)
            assert r4.status_code == 200 and r4.json()["ssn"] == "111-22-3333"

            # status + notes
            r5 = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "screening"})
            assert r5.status_code == 200
            r6 = await c.patch(f"/api/recruitment/applications/{app_id}/notes",
                               headers=headers, json={"notes": "Strong"})
            assert r6.status_code == 200
            # bad status rejected
            r7 = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "bogus"})
            assert r7.status_code == 422

    async def test_driver_role_cannot_access(self, api):
        app, db = api
        acct = await db.create_account("Gate Co")
        driver = await db.create_user(998877, acct.id, role=Role.DRIVER)
        from interfaces.api.auth import create_jwt
        token = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/recruitment/applications",
                            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403


class TestHireLinkage:
    """Hiring an applicant mints a driver invite carrying the source
    application id; redeeming it stamps ``converted_to_user_id`` so the
    application records which driver it became (the round-trip that was
    previously a stub)."""

    async def test_redeem_links_application_to_hired_driver(self, api):
        _, db = api
        acct = await db.create_account("Hire Co")
        recruiter = await db.create_user(900800, acct.id, role=Role.RECRUITER)
        link = await db.create_recruitment_link(acct.id)
        appl = await db.create_driver_application(
            acct.id, link_token=link["token"], reference="APP-HIRE",
            data={"personal": {"first": "Jane", "last": "Roe",
                               "email": "jane@x.com", "ssn": "111-22-3333"},
                  "cdl": {"state": "TX", "class": "A", "number": "X1234567"},
                  "consents": {"truthful": True}},
            docs={},
        )
        # Hire: a driver invite linked back to this application.
        invite = await db.create_invite(
            acct.id, created_by=recruiter.id, role=Role.DRIVER, hours=168,
            source_application_id=appl["id"],
        )
        before = await db.get_driver_application(acct.id, appl["id"])
        assert before["converted_to_user_id"] is None
        # Driver onboards by redeeming → user created → linkage stamped.
        user = await db.redeem_invite(invite.code, telegram_id=900900,
                                      display_name="Jane Roe")
        assert user is not None
        after = await db.get_driver_application(acct.id, appl["id"])
        assert after["converted_to_user_id"] == user.id
        # The now-encrypted CDL blob (licence number) still round-trips.
        assert after["cdl"]["number"] == "X1234567"


class TestPipelineEnforcement:
    """The application lifecycle is server-enforced: 'hired' is reachable
    only via the Hire action, illegal status jumps are rejected, and an
    applicant must be 'approved' before they can be hired."""

    async def _submit(self, c, headers) -> int:
        r = await c.post("/api/recruitment/links", headers=headers,
                         json={"label": "x"})
        token = r.json()["token"]
        r2 = await c.post("/api/recruitment/apply",
                          data={"link_token": token, "application": _app_payload()},
                          files=_GOOD_FILES)
        assert r2.status_code == 200, r2.text
        return r2.json()["application_id"]

    async def test_cannot_set_hired_via_status_patch(self, api):
        app, db = api
        acct = await db.create_account("Pipe A")
        rec = await db.create_user(551001, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            app_id = await self._submit(c, headers)
            r = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                              headers=headers, json={"status": "hired"})
            assert r.status_code == 409
            assert "hire" in r.json()["detail"].lower()

    async def test_illegal_transition_rejected(self, api):
        app, db = api
        acct = await db.create_account("Pipe B")
        rec = await db.create_user(551002, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            app_id = await self._submit(c, headers)
            # submitted → rejected is allowed …
            r1 = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "rejected"})
            assert r1.status_code == 200
            # … but rejected → interview is not (only re-open to screening).
            r2 = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "interview"})
            assert r2.status_code == 409

    async def test_convert_requires_approved(self, api):
        app, db = api
        acct = await db.create_account("Pipe C")
        rec = await db.create_user(551003, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            app_id = await self._submit(c, headers)
            # Hiring straight from 'submitted' is blocked.
            r = await c.post(f"/api/recruitment/applications/{app_id}/convert",
                             headers=headers)
            assert r.status_code == 409
            assert "approved" in r.json()["detail"].lower()
            # Record the required pre-hire checks — 'approved' is gated on them.
            for ck in ("psp", "mvr", "clearinghouse"):
                await c.patch(f"/api/recruitment/applications/{app_id}/vetting",
                              headers=headers, json={"check": ck, "done": True})
            # Walk the pipeline to 'approved', then the hire succeeds.
            for st in ("screening", "interview", "approved"):
                rr = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                                   headers=headers, json={"status": st})
                assert rr.status_code == 200, (st, rr.text)
            r2 = await c.post(f"/api/recruitment/applications/{app_id}/convert",
                              headers=headers)
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "hired"
            assert "/signup/" in r2.json()["invite_link"]
            # And it's now terminal — can't be flipped back.
            r3 = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "rejected"})
            assert r3.status_code == 409


class TestLinkExpiry:
    """Recruiting links auto-close: a token past its expiry no longer
    resolves (rejecting new submissions), while a never-expires link
    (expires_in_days=0/None) keeps working."""

    async def test_link_expiry_enforced(self, api):
        _, db = api
        acct = await db.create_account("Exp Co")
        link = await db.create_recruitment_link(acct.id, expires_in_days=90)
        assert link["expires_at"] is not None
        # Resolves while still valid.
        assert await db.resolve_recruitment_link(link["token"]) is not None
        # Force it past expiry → resolve must reject it.
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await db._db.execute(
            "UPDATE recruitment_links SET expires_at = ? WHERE id = ?",
            (past, link["id"]),
        )
        await db._db.commit()
        assert await db.resolve_recruitment_link(link["token"]) is None
        # A never-expires link (days=0 → NULL) still resolves.
        forever = await db.create_recruitment_link(acct.id, expires_in_days=0)
        assert forever["expires_at"] is None
        assert await db.resolve_recruitment_link(forever["token"]) is not None


class TestDocServing:
    """A reviewer can stream an applicant's uploaded documents back; the
    endpoint is permission-gated, account-scoped, and rejects unknown
    slots — there's no path-traversal surface (slots resolve only against
    the application's own server-generated object ids)."""

    async def test_recruiter_views_doc_others_blocked(self, api):
        app, db = api
        acct = await db.create_account("Doc Co")
        rec = await db.create_user(771122, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]

            # Real doc streams back with its true (sniffed) content type.
            d = await c.get(f"/api/recruitment/applications/{app_id}/docs/medical", headers=headers)
            assert d.status_code == 200
            assert d.headers["content-type"].startswith("application/pdf")
            img = await c.get(f"/api/recruitment/applications/{app_id}/docs/cdlFront", headers=headers)
            assert img.status_code == 200 and img.headers["content-type"].startswith("image/jpeg")

            # Unknown slot → 404 (no oracle, no traversal).
            assert (await c.get(f"/api/recruitment/applications/{app_id}/docs/bogus", headers=headers)).status_code == 404
            # A slot with no uploaded file → 404.
            assert (await c.get(f"/api/recruitment/applications/{app_id}/docs/truckPic", headers=headers)).status_code == 404

            # Driver role is denied.
            driver = await db.create_user(881133, acct.id, role=Role.DRIVER)
            from interfaces.api.auth import create_jwt
            dtok = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
            denied = await c.get(f"/api/recruitment/applications/{app_id}/docs/medical",
                                 headers={"Authorization": f"Bearer {dtok}"})
            assert denied.status_code == 403

    async def test_doc_is_account_scoped(self, api):
        app, db = api
        acct_a = await db.create_account("Doc A")
        acct_b = await db.create_account("Doc B")
        link = await db.create_recruitment_link(acct_a.id)
        rec_b = await db.create_user(772233, acct_b.id, role=Role.RECRUITER)
        headers_b = _recruiter_headers(rec_b, acct_b)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            # Account B's recruiter cannot read account A's application doc.
            other = await c.get(f"/api/recruitment/applications/{app_id}/docs/medical", headers=headers_b)
            assert other.status_code == 404


class TestNotifications:
    """A new submission fans out to every can_recruit_applicants holder on
    their chosen channels.  The dashboard (in-app) channel is exercised
    here; targeting is by permission, and each user's channel set is their
    own preference (default = all three)."""

    async def test_submission_creates_inapp_notification(self, api):
        app, db = api
        acct = await db.create_account("Notify Co")
        rec = await db.create_user(660011, acct.id, role=Role.RECRUITER)
        driver = await db.create_user(660022, acct.id, role=Role.DRIVER)
        rec_headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            assert r.status_code == 200, r.text

            # The recruiter has an unread in-app notification for it.
            n = await c.get("/api/recruitment/notifications", headers=rec_headers)
            assert n.status_code == 200
            data = n.json()
            assert data["unread_count"] == 1
            assert data["items"][0]["reference"].startswith("APP-")

            # A driver (no can_recruit_applicants) can't even reach the inbox.
            from interfaces.api.auth import create_jwt
            dtok = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
            denied = await c.get("/api/recruitment/notifications",
                                 headers={"Authorization": f"Bearer {dtok}"})
            assert denied.status_code == 403

            # Mark all read → unread clears.
            await c.post("/api/recruitment/notifications/read", headers=rec_headers, json={})
            cleared = await c.get("/api/recruitment/notifications", headers=rec_headers)
            assert cleared.json()["unread_count"] == 0

    async def test_channel_prefs_roundtrip_and_optout(self, api):
        app, db = api
        acct = await db.create_account("Pref Co")
        rec = await db.create_user(661111, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Default = all three channels.
            g = await c.get("/api/recruitment/notify-prefs", headers=headers)
            assert set(g.json()["channels"]) == {"telegram", "email", "dashboard"}

            # Opt out of the dashboard channel.
            p = await c.put("/api/recruitment/notify-prefs", headers=headers,
                            json={"channels": ["email"]})
            assert p.status_code == 200 and p.json()["channels"] == ["email"]

            # A new submission now creates NO in-app notification for them.
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            assert r.status_code == 200
            n = await c.get("/api/recruitment/notifications", headers=headers)
            assert n.json()["unread_count"] == 0


class TestVettingGate:
    """'approved' is gated on the required pre-hire checks (PSP / MVR /
    Clearinghouse) being recorded — the consents alone don't suffice."""

    async def test_approve_blocked_until_required_checks_done(self, api):
        app, db = api
        acct = await db.create_account("Vet Co")
        rec = await db.create_user(880011, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            await c.patch(f"/api/recruitment/applications/{app_id}/status",
                          headers=headers, json={"status": "screening"})
            # Approve is blocked — no checks recorded yet.
            blocked = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                                    headers=headers, json={"status": "approved"})
            assert blocked.status_code == 409
            assert "PSP" in blocked.json()["detail"]

            # Unknown check rejected; the three required ones tick fine.
            bad = await c.patch(f"/api/recruitment/applications/{app_id}/vetting",
                                headers=headers, json={"check": "bogus", "done": True})
            assert bad.status_code == 422
            for ck in ("psp", "mvr", "clearinghouse"):
                v = await c.patch(f"/api/recruitment/applications/{app_id}/vetting",
                                  headers=headers, json={"check": ck, "done": True})
                assert v.status_code == 200

            # Now approval succeeds.
            ok = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "approved"})
            assert ok.status_code == 200


class TestRetention:
    """Rejected applications are RETAINED (no premature delete); the
    account purge still cleans up correctly (notification FK cascades)."""

    async def test_rejected_application_is_retained(self, api):
        app, db = api
        acct = await db.create_account("Retain Co")
        rec = await db.create_user(881100, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            rj = await c.patch(f"/api/recruitment/applications/{app_id}/status",
                               headers=headers, json={"status": "rejected"})
            assert rj.status_code == 200
            still = await c.get(f"/api/recruitment/applications/{app_id}", headers=headers)
            assert still.status_code == 200 and still.json()["status"] == "rejected"
        assert any(a["id"] == app_id for a in await db.list_driver_applications(acct.id))

    async def test_notification_fk_cascades_on_app_delete(self, api):
        app, db = api
        acct = await db.create_account("Cascade Co")
        rec = await db.create_user(882200, acct.id, role=Role.RECRUITER)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
        # A notification references the application.
        assert len(await db.list_recruitment_notifications(acct.id, rec.id)) == 1
        # Deleting the application must NOT be blocked by that FK (CASCADE),
        # else the account purge would leave applicant PII behind.
        await db._db.execute("DELETE FROM driver_applications WHERE id = ?", (app_id,))
        await db._db.commit()
        assert await db.list_recruitment_notifications(acct.id, rec.id) == []


class TestDQPacket:
    """The §391.51 packet PDF is downloadable by a recruiter, denied to a
    driver."""

    async def test_packet_pdf(self, api):
        app, db = api
        acct = await db.create_account("Packet Co")
        rec = await db.create_user(883300, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/recruitment/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            pdf = await c.get(f"/api/recruitment/applications/{app_id}/packet.pdf", headers=headers)
            assert pdf.status_code == 200, pdf.text
            assert pdf.headers["content-type"].startswith("application/pdf")
            assert pdf.content[:4] == b"%PDF"

            driver = await db.create_user(883399, acct.id, role=Role.DRIVER)
            from interfaces.api.auth import create_jwt
            dtok = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
            denied = await c.get(f"/api/recruitment/applications/{app_id}/packet.pdf",
                                 headers={"Authorization": f"Bearer {dtok}"})
            assert denied.status_code == 403


class TestLinkAnalytics:
    """Per-link funnel: view pings (oracle-safe, always 204) + submissions
    and hires derived from applications on the token."""

    async def test_views_and_link_stats(self, api):
        app, db = api
        acct = await db.create_account("Analytics Co")
        rec = await db.create_user(990011, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            token = (await c.post("/api/recruitment/links", headers=headers,
                                  json={"label": "TikTok"})).json()["token"]
            # Three public views — all 204.
            for _ in range(3):
                assert (await c.post("/api/recruitment/track-view", json={"token": token})).status_code == 204
            # An unknown token is ALSO 204 (no oracle for which tokens exist).
            assert (await c.post("/api/recruitment/track-view", json={"token": "BOGUS"})).status_code == 204
            # One application through the link.
            await c.post("/api/recruitment/apply",
                         data={"link_token": token, "application": _app_payload()}, files=_GOOD_FILES)

            link = next(l for l in (await c.get("/api/recruitment/links", headers=headers)).json()["items"]
                        if l["token"] == token)
            assert link["view_count"] == 3
            assert link["submissions"] == 1
            assert link["hires"] == 0


class TestDuplicateDetection:
    """Re-applicant detection is RECRUITER-SIDE ONLY — it flags prior
    matches (SSN blind-index / email / phone) but NEVER blocks the public
    submission, and never crosses account boundaries."""

    async def test_reapplicant_flagged_but_never_blocked(self, api):
        app, db = api
        acct = await db.create_account("Dup Co")
        rec = await db.create_user(995511, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_recruitment_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.post("/api/recruitment/apply",
                              data={"link_token": link["token"], "application": _app_payload()},
                              files=_GOOD_FILES)
            id1 = r1.json()["application_id"]
            # Same SSN, different person — the public form MUST still accept it.
            p2 = _app_payload(personal={"first": "Janet", "last": "Roe",
                                        "email": "janet@x.com", "ssn": "111-22-3333"})
            r2 = await c.post("/api/recruitment/apply",
                              data={"link_token": link["token"], "application": p2},
                              files=_GOOD_FILES)
            assert r2.status_code == 200, r2.text   # never blocked
            id2 = r2.json()["application_id"]

            by_id = {i["id"]: i for i in (await c.get("/api/recruitment/applications", headers=headers)).json()["items"]}
            assert by_id[id1]["duplicate"] and by_id[id2]["duplicate"]

            d2 = (await c.get(f"/api/recruitment/applications/{id2}", headers=headers)).json()
            assert any(rel["id"] == id1 for rel in d2["related"])
            assert "ssn_hash" not in d2   # internal index never leaks

    async def test_no_cross_account_match(self, api):
        app, db = api
        acct_a = await db.create_account("Dup A")
        acct_b = await db.create_account("Dup B")
        rec_b = await db.create_user(995522, acct_b.id, role=Role.RECRUITER)
        link_a = await db.create_recruitment_link(acct_a.id)
        link_b = await db.create_recruitment_link(acct_b.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # The SAME applicant (same SSN/email) applies to two carriers.
            await c.post("/api/recruitment/apply",
                         data={"link_token": link_a["token"], "application": _app_payload()}, files=_GOOD_FILES)
            await c.post("/api/recruitment/apply",
                         data={"link_token": link_b["token"], "application": _app_payload()}, files=_GOOD_FILES)
            items_b = (await c.get("/api/recruitment/applications", headers=_recruiter_headers(rec_b, acct_b))).json()["items"]
            # Account B sees no duplicate — the per-account salt isolates them.
            assert not items_b[0]["duplicate"]
