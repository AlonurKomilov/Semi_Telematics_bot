"""Tests for the driver-recruiting intake feature.

Covers:
- infra.file_safety magic-byte validation (the public-upload guard)
- public POST /applications/apply: token gate, file-content validation,
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
                     "employment_verification": True,
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
            r = await c.post("/api/applications/apply",
                             data={"link_token": "BOGUS", "application": _app_payload()},
                             files=_GOOD_FILES)
        assert r.status_code == 404

    async def test_disguised_file_rejected(self, api):
        app, db = api
        acct = await db.create_account("Apply A")
        link = await db.create_application_link(acct.id)
        files = dict(_GOOD_FILES); files["cdl_front"] = ("a.jpg", EXE, "image/jpeg")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=files)
        assert r.status_code == 422
        assert "rejected" in r.json()["detail"].lower()

    async def test_missing_required_doc_422(self, api):
        app, db = api
        acct = await db.create_account("Apply B")
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files={"cdl_front": _GOOD_FILES["cdl_front"]})
        assert r.status_code == 422

    async def test_bad_email_422(self, api):
        app, db = api
        acct = await db.create_account("Apply C")
        link = await db.create_application_link(acct.id)
        payload = _app_payload(personal={"first": "J", "last": "R", "email": "nope"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": payload},
                             files=_GOOD_FILES)
        assert r.status_code == 422

    async def test_missing_certification_422(self, api):
        app, db = api
        acct = await db.create_account("Apply D")
        link = await db.create_application_link(acct.id)
        payload = _app_payload(consents={"truthful": False, "sigMode": "type"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": payload},
                             files=_GOOD_FILES)
        assert r.status_code == 422

    async def test_happy_path_stores_application(self, api):
        app, db = api
        acct = await db.create_account("Apply E")
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
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
        link = await db.create_application_link(acct.id)
        payload = _app_payload(
            addressHistory=[{"addr1": "12 Old Rd", "city": "Reno", "state": "NV",
                             "from": "2022-01", "to": "2024-01"}],
            employment=[{"company": "Acme", "gapExplanation": "commercial school"}],
            disclosureVersion="2026-06-1",
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
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
            r = await c.post("/api/applications/links", headers=headers,
                             json={"label": "Indeed", "source": "indeed"})
            assert r.status_code == 200, r.text
            token = r.json()["token"]

            # public applicant submits through that link
            r2 = await c.post("/api/applications/apply",
                              data={"link_token": token, "application": _app_payload()},
                              files=_GOOD_FILES)
            assert r2.status_code == 200
            app_id = r2.json()["application_id"]

            # recruiter lists + reads detail
            r3 = await c.get("/api/applications", headers=headers)
            assert r3.status_code == 200 and r3.json()["count"] == 1
            r4 = await c.get(f"/api/applications/{app_id}", headers=headers)
            assert r4.status_code == 200 and r4.json()["ssn"] == "111-22-3333"

            # status + notes
            r5 = await c.patch(f"/api/applications/{app_id}/status",
                               headers=headers, json={"status": "screening"})
            assert r5.status_code == 200
            r6 = await c.patch(f"/api/applications/{app_id}/notes",
                               headers=headers, json={"notes": "Strong"})
            assert r6.status_code == 200
            # bad status rejected
            r7 = await c.patch(f"/api/applications/{app_id}/status",
                               headers=headers, json={"status": "bogus"})
            assert r7.status_code == 422

    async def test_driver_role_cannot_access(self, api):
        app, db = api
        acct = await db.create_account("Gate Co")
        driver = await db.create_user(998877, acct.id, role=Role.DRIVER)
        from interfaces.api.auth import create_jwt
        token = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/applications",
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
        link = await db.create_application_link(acct.id)
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
        r = await c.post("/api/applications/links", headers=headers,
                         json={"label": "x"})
        token = r.json()["token"]
        r2 = await c.post("/api/applications/apply",
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
            r = await c.patch(f"/api/applications/{app_id}/status",
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
            r1 = await c.patch(f"/api/applications/{app_id}/status",
                               headers=headers, json={"status": "rejected"})
            assert r1.status_code == 200
            # … but rejected → interview is not (only re-open to screening).
            r2 = await c.patch(f"/api/applications/{app_id}/status",
                               headers=headers, json={"status": "interview"})
            assert r2.status_code == 409

    async def test_convert_requires_approved(self, api):
        """The pipeline gate, now across the recruiting↔onboarding HANDOFF.

        The recruiter walks the application to 'approved' and stops there
        — hiring moved to features/drivers/onboarding/ (2026-07-30)
        because it mints a USER.  So this test drives two actors, and the
        recruiter's own 403 on the hire is part of the contract.
        """
        from interfaces.api.auth import create_jwt
        app, db = api
        acct = await db.create_account("Pipe C")
        rec = await db.create_user(551003, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        hr = await db.create_user(551004, acct.id, role=Role.HR)
        hr_headers = {"Authorization": f"Bearer {create_jwt(hr.telegram_id or 0, acct.id, 'hr', user_id=hr.id)}"}
        convert = lambda aid: f"/api/drivers/onboarding/{aid}/convert"   # noqa: E731

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            app_id = await self._submit(c, headers)
            # The recruiter approves but never hires — that grant is HR's.
            assert (await c.post(convert(app_id), headers=headers)).status_code == 403
            # Hiring straight from 'submitted' is blocked, even for HR.
            r = await c.post(convert(app_id), headers=hr_headers)
            assert r.status_code == 409
            assert "approved" in r.json()["detail"].lower()
            # Record the required pre-hire checks — 'approved' is gated on them.
            for ck in ("psp", "mvr", "clearinghouse"):
                await c.patch(f"/api/applications/{app_id}/vetting",
                              headers=headers, json={"check": ck, "done": True})
            # Walk the pipeline to 'approved', then the handoff succeeds.
            for st in ("screening", "interview", "approved"):
                rr = await c.patch(f"/api/applications/{app_id}/status",
                                   headers=headers, json={"status": st})
                assert rr.status_code == 200, (st, rr.text)
            # It reaches HR's queue WITHOUT the recruiting dashboard grant.
            q = await c.get("/api/drivers/onboarding/queue", headers=hr_headers)
            assert q.status_code == 200, q.text
            assert any(a["id"] == app_id for a in q.json()["applicants"])

            r2 = await c.post(convert(app_id), headers=hr_headers)
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "hired"
            assert "/signup/" in r2.json()["invite_link"]
            # And it's now terminal — can't be flipped back.
            r3 = await c.patch(f"/api/applications/{app_id}/status",
                               headers=headers, json={"status": "rejected"})
            assert r3.status_code == 409


class TestLinkExpiry:
    """Recruiting links auto-close: a token past its expiry no longer
    resolves (rejecting new submissions), while a never-expires link
    (expires_in_days=0/None) keeps working."""

    async def test_link_expiry_enforced(self, api):
        _, db = api
        acct = await db.create_account("Exp Co")
        link = await db.create_application_link(acct.id, expires_in_days=90)
        assert link["expires_at"] is not None
        # Resolves while still valid.
        assert await db.resolve_application_link(link["token"]) is not None
        # Force it past expiry → resolve must reject it.
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await db._db.execute(
            # NB: physical table keeps its legacy name (the feature rename
            # did not migrate live tables); the storage methods are application_*.
            "UPDATE application_links SET expires_at = ? WHERE id = ?",
            (past, link["id"]),
        )
        await db._db.commit()
        assert await db.resolve_application_link(link["token"]) is None
        # A never-expires link (days=0 → NULL) still resolves.
        forever = await db.create_application_link(acct.id, expires_in_days=0)
        assert forever["expires_at"] is None
        assert await db.resolve_application_link(forever["token"]) is not None


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
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]

            # Real doc streams back with its true (sniffed) content type.
            d = await c.get(f"/api/applications/{app_id}/docs/medical", headers=headers)
            assert d.status_code == 200
            assert d.headers["content-type"].startswith("application/pdf")
            img = await c.get(f"/api/applications/{app_id}/docs/cdlFront", headers=headers)
            assert img.status_code == 200 and img.headers["content-type"].startswith("image/jpeg")

            # Unknown slot → 404 (no oracle, no traversal).
            assert (await c.get(f"/api/applications/{app_id}/docs/bogus", headers=headers)).status_code == 404
            # A slot with no uploaded file → 404.
            assert (await c.get(f"/api/applications/{app_id}/docs/truckPic", headers=headers)).status_code == 404

            # Driver role is denied.
            driver = await db.create_user(881133, acct.id, role=Role.DRIVER)
            from interfaces.api.auth import create_jwt
            dtok = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
            denied = await c.get(f"/api/applications/{app_id}/docs/medical",
                                 headers={"Authorization": f"Bearer {dtok}"})
            assert denied.status_code == 403

    async def test_doc_is_account_scoped(self, api):
        app, db = api
        acct_a = await db.create_account("Doc A")
        acct_b = await db.create_account("Doc B")
        link = await db.create_application_link(acct_a.id)
        rec_b = await db.create_user(772233, acct_b.id, role=Role.RECRUITER)
        headers_b = _recruiter_headers(rec_b, acct_b)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            # Account B's recruiter cannot read account A's application doc.
            other = await c.get(f"/api/applications/{app_id}/docs/medical", headers=headers_b)
            assert other.status_code == 404


class TestNotifications:
    """A new submission fans out to every can_manage_applications holder on
    their chosen channels.  The dashboard (in-app) channel is exercised
    here; targeting is by permission, and each user's channel set is their
    own preference (default = all three)."""

    async def test_submission_creates_inapp_notification(self, api):
        app, db = api
        acct = await db.create_account("Notify Co")
        rec = await db.create_user(660011, acct.id, role=Role.RECRUITER)
        driver = await db.create_user(660022, acct.id, role=Role.DRIVER)
        rec_headers = _recruiter_headers(rec, acct)
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            assert r.status_code == 200, r.text

            # The notice lands in the SHARED inbox under this feature's
            # source — one store, so both bells read the same row.
            n = await c.get("/api/notifications/inbox?source=applications",
                            headers=rec_headers)
            assert n.status_code == 200, n.text
            notices = n.json()["notices"]
            assert len(notices) == 1
            notice = notices[0]
            assert notice["category"] == "applications.received"
            assert notice["source"] == "applications"
            assert notice["read"] is False
            # The object chip carries the reference; the deep link the row.
            assert notice["context"].startswith("APP-")
            assert "?app=" in notice["url"]

            # A driver holds no can_manage_applications, so nothing was
            # addressed to them — the inbox is per-user, so their own
            # feed is simply empty (no 403 to rely on: everyone has a
            # notifications door, targeting is what scopes it).
            from interfaces.api.auth import create_jwt
            dtok = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
            theirs = await c.get("/api/notifications/inbox?source=applications",
                                 headers={"Authorization": f"Bearer {dtok}"})
            assert theirs.status_code == 200
            assert theirs.json()["notices"] == []

            # Mark read → the row clears for BOTH bells at once.
            await c.post("/api/notifications/inbox/read", headers=rec_headers,
                         json={"ids": [notice["id"]]})
            cleared = await c.get("/api/notifications/inbox?source=applications",
                                  headers=rec_headers)
            assert cleared.json()["notices"][0]["read"] is True

    async def test_channel_prefs_roundtrip_and_optout(self, api):
        app, db = api
        acct = await db.create_account("Pref Co")
        rec = await db.create_user(661111, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Default = all three channels.
            g = await c.get("/api/applications/notify-prefs", headers=headers)
            assert set(g.json()["channels"]) == {"telegram", "email", "dashboard"}

            # Opt out of the dashboard channel.
            p = await c.put("/api/applications/notify-prefs", headers=headers,
                            json={"channels": ["email"]})
            assert p.status_code == 200 and p.json()["channels"] == ["email"]

            # A new submission now creates NO in-app notification for them.
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            assert r.status_code == 200
            n = await c.get("/api/notifications/inbox?source=applications",
                            headers=headers)
            assert n.json()["notices"] == []


class TestVettingGate:
    """'approved' is gated on the required pre-hire checks (PSP / MVR /
    Clearinghouse) being recorded — the consents alone don't suffice."""

    async def test_approve_blocked_until_required_checks_done(self, api):
        app, db = api
        acct = await db.create_account("Vet Co")
        rec = await db.create_user(880011, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            await c.patch(f"/api/applications/{app_id}/status",
                          headers=headers, json={"status": "screening"})
            # Approve is blocked — no checks recorded yet.
            blocked = await c.patch(f"/api/applications/{app_id}/status",
                                    headers=headers, json={"status": "approved"})
            assert blocked.status_code == 409
            assert "PSP" in blocked.json()["detail"]

            # Unknown check rejected; the three required ones tick fine.
            bad = await c.patch(f"/api/applications/{app_id}/vetting",
                                headers=headers, json={"check": "bogus", "done": True})
            assert bad.status_code == 422
            for ck in ("psp", "mvr", "clearinghouse"):
                v = await c.patch(f"/api/applications/{app_id}/vetting",
                                  headers=headers, json={"check": ck, "done": True})
                assert v.status_code == 200

            # Now approval succeeds.
            ok = await c.patch(f"/api/applications/{app_id}/status",
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
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            rj = await c.patch(f"/api/applications/{app_id}/status",
                               headers=headers, json={"status": "rejected"})
            assert rj.status_code == 200
            still = await c.get(f"/api/applications/{app_id}", headers=headers)
            assert still.status_code == 200 and still.json()["status"] == "rejected"
        assert any(a["id"] == app_id for a in await db.list_driver_applications(acct.id))

    async def test_notification_fk_cascades_on_app_delete(self, api):
        app, db = api
        acct = await db.create_account("Cascade Co")
        rec = await db.create_user(882200, acct.id, role=Role.RECRUITER)
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
        # A row in the retired notice table references the application.
        # Nothing writes it any more (recruiting notices ride the shared
        # notification_inbox), so seed one here: the FK it declares is
        # what this test exists to pin, until the table is dropped.
        await db.create_application_notification(
            acct.id, rec.id, application_id=app_id,
            reference="REF", title="New driver application")
        assert len(await db.list_application_notifications(acct.id, rec.id)) == 1
        # Deleting the application must NOT be blocked by that FK (CASCADE),
        # else the account purge would leave applicant PII behind.
        await db._db.execute("DELETE FROM driver_applications WHERE id = ?", (app_id,))
        await db._db.commit()
        assert await db.list_application_notifications(acct.id, rec.id) == []


class TestDQPacket:
    """The §391.51 packet PDF is downloadable by a recruiter, denied to a
    driver."""

    async def test_packet_pdf(self, api):
        app, db = api
        acct = await db.create_account("Packet Co")
        rec = await db.create_user(883300, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload()},
                             files=_GOOD_FILES)
            app_id = r.json()["application_id"]
            pdf = await c.get(f"/api/applications/{app_id}/packet.pdf", headers=headers)
            assert pdf.status_code == 200, pdf.text
            assert pdf.headers["content-type"].startswith("application/pdf")
            assert pdf.content[:4] == b"%PDF"

            driver = await db.create_user(883399, acct.id, role=Role.DRIVER)
            from interfaces.api.auth import create_jwt
            dtok = create_jwt(driver.telegram_id, acct.id, "driver", user_id=driver.id)
            denied = await c.get(f"/api/applications/{app_id}/packet.pdf",
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
            token = (await c.post("/api/applications/links", headers=headers,
                                  json={"label": "TikTok"})).json()["token"]
            # Three public views — all 204.
            for _ in range(3):
                assert (await c.post("/api/applications/track-view", json={"token": token})).status_code == 204
            # An unknown token is ALSO 204 (no oracle for which tokens exist).
            assert (await c.post("/api/applications/track-view", json={"token": "BOGUS"})).status_code == 204
            # One application through the link.
            await c.post("/api/applications/apply",
                         data={"link_token": token, "application": _app_payload()}, files=_GOOD_FILES)

            link = next(lk for lk in (await c.get("/api/applications/links", headers=headers)).json()["items"]
                        if lk["token"] == token)
            assert link["view_count"] == 3
            assert link["submissions"] == 1
            assert link["hires"] == 0


class TestDuplicateDetection:
    """Re-applicant detection is RECRUITER-SIDE ONLY — it flags prior
    matches (SSN blind-index / email / phone) but NEVER blocks the public
    submission, and never crosses account boundaries."""

    async def test_reapplicant_flagged_but_never_blocked(self, api, monkeypatch):
        # SSN matching needs a key (the blind index); the suite runs keyless
        # by default, so set one to exercise the SSN path specifically.
        monkeypatch.setenv("ENCRYPTION_KEY", "dup-test-key-32-chars-minimum-aaaaaa")
        app, db = api
        acct = await db.create_account("Dup Co")
        rec = await db.create_user(995511, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r1 = await c.post("/api/applications/apply",
                              data={"link_token": link["token"], "application": _app_payload()},
                              files=_GOOD_FILES)
            id1 = r1.json()["application_id"]
            # Same SSN, DIFFERENT email/phone — proves it matched on the SSN,
            # and the public form MUST still accept it (never blocked).
            p2 = _app_payload(personal={"first": "Janet", "last": "Roe",
                                        "email": "janet@x.com", "ssn": "111-22-3333"})
            r2 = await c.post("/api/applications/apply",
                              data={"link_token": link["token"], "application": p2},
                              files=_GOOD_FILES)
            assert r2.status_code == 200, r2.text   # never blocked
            id2 = r2.json()["application_id"]

            by_id = {i["id"]: i for i in (await c.get("/api/applications", headers=headers)).json()["items"]}
            assert by_id[id1]["duplicate"] and by_id[id2]["duplicate"]

            d2 = (await c.get(f"/api/applications/{id2}", headers=headers)).json()
            assert any(rel["id"] == id1 for rel in d2["related"])
            assert "ssn_hash" not in d2   # internal index never leaks

    async def test_no_cross_account_match(self, api):
        app, db = api
        acct_a = await db.create_account("Dup A")
        acct_b = await db.create_account("Dup B")
        rec_b = await db.create_user(995522, acct_b.id, role=Role.RECRUITER)
        link_a = await db.create_application_link(acct_a.id)
        link_b = await db.create_application_link(acct_b.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # The SAME applicant (same SSN/email) applies to two carriers.
            await c.post("/api/applications/apply",
                         data={"link_token": link_a["token"], "application": _app_payload()}, files=_GOOD_FILES)
            await c.post("/api/applications/apply",
                         data={"link_token": link_b["token"], "application": _app_payload()}, files=_GOOD_FILES)
            items_b = (await c.get("/api/applications", headers=_recruiter_headers(rec_b, acct_b))).json()["items"]
            # Account B sees no duplicate — the per-account salt isolates them.
            assert not items_b[0]["duplicate"]


class TestHoneypot:
    """A filled honeypot field is silently dropped: the response LOOKS like
    success (no signal to the bot) but nothing is stored."""

    async def test_honeypot_drops_silently(self, api):
        app, db = api
        acct = await db.create_account("Honeypot Co")
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/apply",
                             data={"link_token": link["token"], "application": _app_payload(),
                                   "hp": "http://spam.example"},
                             files=_GOOD_FILES)
            assert r.status_code == 200 and r.json()["success"] is True
            assert r.json()["application_id"] == 0
        # Nothing persisted.
        assert await db.list_driver_applications(acct.id) == []
        # A normal submit (empty honeypot) still stores.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r2 = await c.post("/api/applications/apply",
                              data={"link_token": link["token"], "application": _app_payload(), "hp": ""},
                              files=_GOOD_FILES)
            assert r2.status_code == 200
        assert len(await db.list_driver_applications(acct.id)) == 1


class TestStatusCheck:
    """Public self-service status: two-factor (reference + email), uniform
    not-found on any mismatch, case-insensitive."""

    async def test_two_factor_lookup(self, api):
        app, db = api
        acct = await db.create_account("Status Co")
        link = await db.create_application_link(acct.id)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ref = (await c.post("/api/applications/apply",
                                data={"link_token": link["token"], "application": _app_payload()},
                                files=_GOOD_FILES)).json()["reference"]

            ok = await c.post("/api/applications/application-status",
                              json={"reference": ref, "email": "jane@x.com"})
            assert ok.status_code == 200
            assert ok.json()["found"] is True and ok.json()["status"] == "submitted"

            # Wrong email → uniform not-found (no oracle).
            assert (await c.post("/api/applications/application-status",
                                 json={"reference": ref, "email": "nope@x.com"})).json()["found"] is False
            # Unknown reference → not-found.
            assert (await c.post("/api/applications/application-status",
                                 json={"reference": "APP-000000", "email": "jane@x.com"})).json()["found"] is False
            # Case-insensitive on both factors.
            assert (await c.post("/api/applications/application-status",
                                 json={"reference": ref.lower(), "email": "JANE@X.COM"})).json()["found"] is True


class TestBulkStatus:
    """Bulk move (table triage) enforces the same per-app rules as the
    single PATCH: illegal jumps + the 'approved' vetting gate are skipped
    (with reasons), and 'hired' is never reachable in bulk."""

    async def _submit(self, c, db, acct) -> int:
        link = await db.create_application_link(acct.id)
        r = await c.post("/api/applications/apply",
                         data={"link_token": link["token"], "application": _app_payload()},
                         files=_GOOD_FILES)
        return r.json()["application_id"]

    async def test_bulk_reject_then_illegal_then_hired(self, api):
        app, db = api
        acct = await db.create_account("Bulk Co")
        rec = await db.create_user(996611, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ids = [await self._submit(c, db, acct) for _ in range(3)]

            r = await c.post("/api/applications/bulk-status",
                             headers=headers, json={"ids": ids, "status": "rejected"})
            assert r.status_code == 200
            assert set(r.json()["updated"]) == set(ids) and r.json()["skipped"] == []

            # All 'rejected' now → only re-open to 'screening'; 'interview' is illegal.
            r2 = await c.post("/api/applications/bulk-status",
                              headers=headers, json={"ids": ids, "status": "interview"})
            assert len(r2.json()["skipped"]) == 3 and not r2.json()["updated"]

            # 'hired' is refused outright.
            r3 = await c.post("/api/applications/bulk-status",
                              headers=headers, json={"ids": ids, "status": "hired"})
            assert r3.status_code == 409

    async def test_bulk_approve_blocked_by_vetting(self, api):
        app, db = api
        acct = await db.create_account("Bulk Vet Co")
        rec = await db.create_user(996622, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            ids = [await self._submit(c, db, acct) for _ in range(2)]
            r = await c.post("/api/applications/bulk-status",
                             headers=headers, json={"ids": ids, "status": "approved"})
            assert not r.json()["updated"] and len(r.json()["skipped"]) == 2
            assert all("vetting" in s["reason"] for s in r.json()["skipped"])


class TestLinkCompany:
    """A recruiting link can be branded for a sub-company (carrier): the
    recruiter picks from a read-only company list, the link carries the
    company_id, and the link list surfaces the carrier name."""

    async def test_company_selection_on_links(self, api):
        app, db = api
        acct = await db.create_account("Brand Co")
        co = await db.add_company(acct.id, "PTG", display_name="Premier Trucking Group")
        rec = await db.create_user(701701, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Recruiter can read the slim company picker (no Samsara key).
            comps = (await c.get("/api/applications/companies", headers=headers)).json()["items"]
            assert any(x["id"] == co.id and x["display_name"] == "Premier Trucking Group" for x in comps)

            # Create a link branded for that carrier.
            r = await c.post("/api/applications/links", headers=headers,
                             json={"label": "Indeed", "company_id": co.id})
            assert r.status_code == 200 and r.json()["company_id"] == co.id

            # The link list surfaces the carrier name.
            link = next(l for l in (await c.get("/api/applications/links", headers=headers)).json()["items"]
                        if l["company_id"] == co.id)
            assert link["company_name"] == "Premier Trucking Group"

            # A company from ANOTHER account is rejected (account-scoped).
            other = await db.create_account("Other Co")
            other_co = await db.add_company(other.id, "XYZ")
            bad = await c.post("/api/applications/links", headers=headers, json={"company_id": other_co.id})
            assert bad.status_code == 422

            # A driver can't reach the picker.
            from interfaces.api.auth import create_jwt
            drv = await db.create_user(701702, acct.id, role=Role.DRIVER)
            dtok = create_jwt(drv.telegram_id, acct.id, "driver", user_id=drv.id)
            denied = await c.get("/api/applications/companies", headers={"Authorization": f"Bearer {dtok}"})
            assert denied.status_code == 403


class TestApplyBranding:
    """Step 3: a branded link surfaces the carrier on the public apply form
    (public /brand endpoint, no Samsara key), stamps the carrier onto the
    submitted application, and the DQ packet names that carrier."""

    async def test_public_brand_and_carrier_stamp(self, api):
        app, db = api
        acct = await db.create_account("Brand Co")
        co = await db.add_company(
            acct.id, "PTG", samsara_api_key="SUPER-SECRET-SAMSARA-KEY",
            display_name="Premier Trucking Group", mc_number="123456",
            usdot_number="7891011")
        await db.update_company(co.id, account_id=acct.id, brand_color="#0a84ff")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            branded = await db.create_application_link(acct.id, company_id=co.id)
            generic = await db.create_application_link(acct.id)

            # Public brand endpoint returns carrier display fields — NEVER the key.
            r = await c.get(f"/api/applications/brand?token={branded['token']}")
            assert r.status_code == 200
            comp = r.json()["company"]
            assert comp["name"] == "Premier Trucking Group"
            assert comp["mc_number"] == "123456" and comp["usdot_number"] == "7891011"
            assert comp["brand_color"] == "#0a84ff"
            assert "SAMSARA" not in r.text.upper() and "SUPER-SECRET" not in r.text

            # Generic link + unknown token → null (no oracle), always 200.
            assert (await c.get(f"/api/applications/brand?token={generic['token']}")).json()["company"] is None
            unknown = await c.get("/api/applications/brand?token=nope-not-a-real-token")
            assert unknown.status_code == 200 and unknown.json()["company"] is None

            # Submitting through the branded link stamps company_id on the app.
            sub = await c.post("/api/applications/apply",
                               data={"link_token": branded["token"], "application": _app_payload()},
                               files=_GOOD_FILES)
            assert sub.status_code == 200, sub.text
            full = await db.get_driver_application(acct.id, sub.json()["application_id"])
            assert full["company_id"] == co.id

            # The DQ packet downloads (carrier path executes) for the recruiter.
            rec = await db.create_user(720009, acct.id, role=Role.RECRUITER)
            pkt = await c.get(f"/api/applications/{sub.json()['application_id']}/packet.pdf",
                              headers=_recruiter_headers(rec, acct))
            assert pkt.status_code == 200 and pkt.content[:4] == b"%PDF"

    def test_report_renders_carrier_header(self):
        """The DQ report uses the carrier identity when given one."""
        from features.applications.report import build_dq_packet_pdf
        buf = build_dq_packet_pdf(
            {"reference": "APP-1", "status": "submitted", "personal": {"first": "A", "last": "B"}},
            account_name="Brand Co", carrier_name="Premier Trucking Group",
            carrier_mc="123456", carrier_dot="7891011")
        assert buf.getvalue()[:4] == b"%PDF" and len(buf.getvalue()) > 800


class TestRecruiterBrandEdit:
    """A recruiter can fix a carrier's COSMETIC brand (logo / colour /
    contact) from the applications surface — but never the Samsara key or
    the owner-verified identity (code / MC / DOT / legal name)."""

    async def test_cosmetic_brand_touchup(self, api):
        app, db = api
        acct = await db.create_account("Brand Co")
        co = await db.add_company(
            acct.id, "PTG", samsara_api_key="SUPER-SECRET-KEY",
            display_name="Premier Trucking Group", mc_number="123456", usdot_number="789")
        rec = await db.create_user(730001, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # The picker exposes preview fields (mc/dot/website/phone) — no key.
            resp = await c.get("/api/applications/companies", headers=headers)
            row = next(x for x in resp.json()["items"] if x["id"] == co.id)
            assert row["mc_number"] == "123456" and row["usdot_number"] == "789"
            assert "SUPER-SECRET" not in resp.text

            # PATCH the cosmetic brand.
            r = await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                              json={"brand_color": "#0a84ff", "website": "ptg.example", "phone": "555-0100"})
            assert r.status_code == 200
            fresh = await db.get_company_in_account(acct.id, co.id)
            assert fresh.brand_color == "#0a84ff" and fresh.website == "ptg.example" and fresh.phone == "555-0100"
            # Owner-managed identity + Samsara key are UNTOUCHED.
            assert fresh.samsara_api_key == "SUPER-SECRET-KEY"
            assert fresh.mc_number == "123456" and fresh.display_name == "Premier Trucking Group"

            # Bad colour is rejected by the schema.
            assert (await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                                  json={"brand_color": "blue"})).status_code == 422

            # Logo upload → serve → remove.
            up = await c.post(f"/api/applications/companies/{co.id}/logo", headers=headers,
                              files={"file": ("logo.jpg", JPG, "image/jpeg")})
            assert up.status_code == 200
            assert (await db.get_company_in_account(acct.id, co.id)).logo_object_id
            served = await c.get(f"/api/applications/companies/{co.id}/logo", headers=headers)
            assert served.status_code == 200 and served.headers["content-type"].startswith("image/")
            assert (await c.delete(f"/api/applications/companies/{co.id}/logo", headers=headers)).status_code == 200
            assert not (await db.get_company_in_account(acct.id, co.id)).logo_object_id

            # A disguised non-image is rejected.
            bad = await c.post(f"/api/applications/companies/{co.id}/logo", headers=headers,
                               files={"file": ("x.pdf", PDF, "application/pdf")})
            assert bad.status_code == 422

            # A company in ANOTHER account → 404 (account-scoped).
            other = await db.create_account("Other")
            other_co = await db.add_company(other.id, "XX")
            assert (await c.patch(f"/api/applications/companies/{other_co.id}/brand", headers=headers,
                                  json={"brand_color": "#111111"})).status_code == 404

            # A driver can't touch carrier brand.
            from interfaces.api.auth import create_jwt
            drv = await db.create_user(730002, acct.id, role=Role.DRIVER)
            dtok = create_jwt(drv.telegram_id, acct.id, "driver", user_id=drv.id)
            denied = await c.post(f"/api/applications/companies/{co.id}/logo",
                                  headers={"Authorization": f"Bearer {dtok}"},
                                  files={"file": ("l.jpg", JPG, "image/jpeg")})
            assert denied.status_code == 403


class TestApplicationsAITool:
    """The AI tool returns pipeline counts + a no-PII applicant list,
    honours the status filter, and degrades safely without context."""

    async def test_pipeline_summary_and_pii_safety(self, api):
        from features.applications.ai_tool import get_driver_applications
        _, db = api
        acct = await db.create_account("AI Co")
        link = await db.create_application_link(acct.id)
        for ref, st in (("APP-S1", "submitted"), ("APP-S2", "screening")):
            a = await db.create_driver_application(
                acct.id, link_token=link["token"], reference=ref,
                data={"personal": {"first": "Jane", "last": "Roe",
                                   "email": "j@x.com", "ssn": "111-22-3333",
                                   "city": "Austin", "state": "TX"},
                      "cdl": {"class": "A"},
                      "consents": {"truthful": True}},
                docs={},
            )
            if st != "submitted":
                await db.update_application_status(acct.id, a["id"], st)

        out = await get_driver_applications({}, None, account_id=acct.id, db=db)
        assert out["total"] == 2
        assert out["by_stage"]["submitted"] == 1
        assert out["by_stage"]["screening"] == 1
        assert len(out["applications"]) == 2
        # PII-safe: the encrypted identifiers must never appear in the output.
        blob = json.dumps(out).lower()
        assert "111-22-3333" not in blob
        assert "ssn" not in blob and "dob" not in blob

        # status filter narrows the listed applicants (not the counts).
        only = await get_driver_applications(
            {"status": "screening"}, None, account_id=acct.id, db=db)
        assert only["total"] == 2  # counts still cover all
        assert len(only["applications"]) == 1
        assert only["applications"][0]["stage"] == "screening"

        # No context → safe error, never a crash.
        guard = await get_driver_applications({}, None, account_id=None, db=None)
        assert "error" in guard


class TestBrandContent:
    """Step 4: recruiter-editable apply-form content (headline / perks /
    hero photo) round-trips to the public brand + banner endpoints."""

    async def test_headline_perks_banner(self, api):
        app, db = api
        acct = await db.create_account("Content Co")
        co = await db.add_company(acct.id, "PTG", display_name="Premier Trucking Group")
        rec = await db.create_user(740001, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Recruiter sets the apply-form pitch + selling points.
            r = await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                              json={"headline": "Top pay. Home weekly.",
                                    "perks": "$0.65/mile\nHome weekends\n2022+ trucks"})
            assert r.status_code == 200
            # Upload a hero photo.
            up = await c.post(f"/api/applications/companies/{co.id}/banner", headers=headers,
                              files={"file": ("hero.jpg", JPG, "image/jpeg")})
            assert up.status_code == 200

            # Public brand surfaces headline + perks (as a clean list) + has_banner.
            link = await db.create_application_link(acct.id, company_id=co.id)
            brand = (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]
            assert brand["headline"] == "Top pay. Home weekly."
            assert brand["perks"] == ["$0.65/mile", "Home weekends", "2022+ trucks"]
            assert brand["has_banner"] is True

            # Public banner serves the photo, then 404s after removal.
            served = await c.get(f"/api/applications/brand-banner?token={link['token']}")
            assert served.status_code == 200 and served.headers["content-type"].startswith("image/")
            assert (await c.delete(f"/api/applications/companies/{co.id}/banner", headers=headers)).status_code == 200
            assert (await c.get(f"/api/applications/brand-banner?token={link['token']}")).status_code == 404

            # A disguised non-image hero is rejected.
            bad = await c.post(f"/api/applications/companies/{co.id}/banner", headers=headers,
                               files={"file": ("x.exe", EXE, "image/jpeg")})
            assert bad.status_code == 422


class TestGateRequirements:
    """Per-carrier pre-qual thresholds (experience / age / CDL class)
    round-trip to the public brand, default to the old hardcoded gate, and
    reject out-of-range values."""

    async def test_requirements_round_trip(self, api):
        app, db = api
        acct = await db.create_account("Req Co")
        co = await db.add_company(acct.id, "RQ", display_name="Req Carrier")
        rec = await db.create_user(750001, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            link = await db.create_application_link(acct.id, company_id=co.id)
            # Defaults match the old hardcoded gate (1 / 21 / A).
            b0 = (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]
            assert b0["req_experience_years"] == 1 and b0["req_min_age"] == 21 and b0["req_cdl_class"] == "A"

            # Recruiter raises the bar for this carrier.
            r = await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                              json={"req_experience_years": 2, "req_min_age": 23, "req_cdl_class": "B"})
            assert r.status_code == 200
            b1 = (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]
            assert b1["req_experience_years"] == 2 and b1["req_min_age"] == 23 and b1["req_cdl_class"] == "B"

            # Out-of-range / invalid values are rejected by the schema.
            for bad in ({"req_cdl_class": "D"}, {"req_min_age": 12}, {"req_experience_years": 99}):
                assert (await c.patch(f"/api/applications/companies/{co.id}/brand",
                                      headers=headers, json=bad)).status_code == 422

            # Form base theme: defaults light, round-trips dark, rejects junk.
            assert b0["form_theme"] == "light"
            assert (await c.patch(f"/api/applications/companies/{co.id}/brand",
                                  headers=headers, json={"form_theme": "dark"})).status_code == 200
            b3 = (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]
            assert b3["form_theme"] == "dark"
            assert (await c.patch(f"/api/applications/companies/{co.id}/brand",
                                  headers=headers, json={"form_theme": "blue"})).status_code == 422

            # Surface colour (derives the whole neutral palette) round-trips.
            assert b0["surface_color"] == ""
            assert (await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                                  json={"surface_color": "#0f172a"})).status_code == 200
            assert (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]["surface_color"] == "#0f172a"

            # Extra colours: header band + page bg + heading round-trip; bad hex → 422.
            assert b0["header_color"] == "" and b0["bg_color"] == "" and b0["heading_color"] == ""
            assert (await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                                  json={"header_color": "#112233", "bg_color": "#f5f5f5",
                                        "heading_color": "#abcdef"})).status_code == 200
            b4 = (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]
            assert b4["header_color"] == "#112233" and b4["bg_color"] == "#f5f5f5"
            assert b4["heading_color"] == "#abcdef"
            assert (await c.patch(f"/api/applications/companies/{co.id}/brand",
                                  headers=headers, json={"header_color": "navy"})).status_code == 422


class TestLinkEditDelete:
    """An existing link can be edited (label/source/carrier/expiry) and
    deleted; deleting it keeps any submitted applications (token-keyed)."""

    async def test_update_and_delete(self, api):
        app, db = api
        acct = await db.create_account("Edit Co")
        co = await db.add_company(acct.id, "PTG", display_name="Premier")
        rec = await db.create_user(760001, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            link = await db.create_application_link(acct.id, label="Old", source="indeed")

            # Edit label / source / carrier (only the given fields change).
            r = await c.patch(f"/api/applications/links/{link['id']}", headers=headers,
                              json={"label": "New", "source": "fb", "company_id": co.id})
            assert r.status_code == 200
            row = next(x for x in (await c.get("/api/applications/links", headers=headers)).json()["items"]
                       if x["id"] == link["id"])
            assert row["label"] == "New" and row["source"] == "fb"
            assert row["company_id"] == co.id and row["company_name"] == "Premier"

            # company_id: null clears the carrier.
            await c.patch(f"/api/applications/links/{link['id']}", headers=headers, json={"company_id": None})
            row2 = next(x for x in (await c.get("/api/applications/links", headers=headers)).json()["items"]
                        if x["id"] == link["id"])
            assert row2["company_id"] is None

            # A foreign company is rejected.
            other = await db.create_account("Other")
            other_co = await db.add_company(other.id, "XX")
            assert (await c.patch(f"/api/applications/links/{link['id']}", headers=headers,
                                  json={"company_id": other_co.id})).status_code == 422

            # Submit through the link, then delete it — the application survives.
            sub = await c.post("/api/applications/apply",
                               data={"link_token": link["token"], "application": _app_payload()},
                               files=_GOOD_FILES)
            assert sub.status_code == 200
            app_id = sub.json()["application_id"]
            assert (await c.delete(f"/api/applications/links/{link['id']}", headers=headers)).status_code == 200
            assert all(x["id"] != link["id"]
                       for x in (await c.get("/api/applications/links", headers=headers)).json()["items"])
            assert await db.get_driver_application(acct.id, app_id) is not None

            # A link in another account can't be touched (404, not 200).
            foreign = await db.create_application_link(other.id)
            assert (await c.delete(f"/api/applications/links/{foreign['id']}", headers=headers)).status_code == 404
            assert (await c.patch(f"/api/applications/links/{foreign['id']}", headers=headers,
                                  json={"label": "x"})).status_code == 404


class TestConsentDocuments:
    """Legal/compliance fields fill the FMCSA/FCRA disclosures, and the new
    Employee-Verification (§391.23) is a REQUIRED consent."""

    async def test_legal_fields_and_required_consent(self, api):
        app, db = api
        acct = await db.create_account("Doc Co")
        co = await db.add_company(acct.id, "DC", display_name="Doc Carrier")
        rec = await db.create_user(770001, acct.id, role=Role.RECRUITER)
        headers = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Recruiter fills the legal/compliance blanks.
            r = await c.patch(f"/api/applications/companies/{co.id}/brand", headers=headers,
                              json={"legal_address": "1 Main St, Cincinnati, OH 45215",
                                    "compliance_email": "safety@x.com", "cra_name": "TR Information Services",
                                    "cra_phone": "800-894-9141", "cra_address": "PO Box 780254, Orlando, FL",
                                    "cra_site": "fullsearch.com"})
            assert r.status_code == 200

            # The public brand surfaces them so the form fills the disclosures.
            link = await db.create_application_link(acct.id, company_id=co.id)
            comp = (await c.get(f"/api/applications/brand?token={link['token']}")).json()["company"]
            assert comp["legal_address"].startswith("1 Main St") and comp["cra_name"] == "TR Information Services"

            # employment_verification is now REQUIRED — submit without it → 422.
            missing = _app_payload(consents={"truthful": True, "psp": True, "mvr": True,
                                             "clearinghouse": True, "fcra": True, "drug": True,
                                             "sigMode": "type", "sigName": "Jane Roe"})
            r2 = await c.post("/api/applications/apply",
                              data={"link_token": link["token"], "application": missing}, files=_GOOD_FILES)
            assert r2.status_code == 422 and "employment_verification" in r2.text

            # With it → accepted (and stored).
            r3 = await c.post("/api/applications/apply",
                              data={"link_token": link["token"], "application": _app_payload()}, files=_GOOD_FILES)
            assert r3.status_code == 200, r3.text
            full = await db.get_driver_application(acct.id, r3.json()["application_id"])
            assert full["consents"].get("employment_verification") is True


class TestClosedLinkAndLegalIdentity:
    """The public /brand contract added for two ethics fixes: telling an
    applicant a posting is closed BEFORE they fill in a DOT file, and never
    rendering an FCRA/§391.23 authorisation with no named counterparty."""

    async def test_brand_reports_link_state_and_a_legal_name(self, api):
        app, db = api
        acct = await db.create_account("Premier Trucking Group Inc")
        rec = await db.create_user(770001, acct.id, role=Role.RECRUITER)
        h = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            token = (await c.post("/api/applications/links", headers=h,
                                  json={"label": "Indeed"})).json()["token"]

            live = await c.get(f"/api/applications/brand?token={token}")
            assert live.status_code == 200
            assert live.json()["link_state"] == "ok"
            # A generic link has no company, but the consent documents still
            # need a party to name — the account's registered identity.
            assert live.json()["legal_name"] == "Premier Trucking Group Inc"

    async def test_dead_link_is_closed_not_a_blank_form(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        rec = await db.create_user(770002, acct.id, role=Role.RECRUITER)
        h = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            created = (await c.post("/api/applications/links", headers=h,
                                    json={"label": "Indeed"})).json()
            await c.post(f"/api/applications/links/{created['id']}/revoke", headers=h)

            dead = await c.get(f"/api/applications/brand?token={created['token']}")
            # Still 200 — no oracle for token existence — but the form now
            # knows to stop before collecting an SSN it can't submit.
            assert dead.status_code == 200
            assert dead.json()["link_state"] == "closed"
            # Coarse on purpose: unknown tokens look identical to revoked.
            unknown = await c.get("/api/applications/brand?token=nope-nope")
            assert unknown.status_code == 200
            assert unknown.json()["link_state"] == "closed"

    async def test_list_reports_the_real_total_not_the_page(self, api):
        """Past the page limit every count on the page describes the loaded
        slice; the UI can only say so if the server tells it the truth."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        rec = await db.create_user(770003, acct.id, role=Role.RECRUITER)
        h = _recruiter_headers(rec, acct)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            token = (await c.post("/api/applications/links", headers=h,
                                  json={"label": "x"})).json()["token"]
            for i in range(3):
                await c.post("/api/applications/apply", data={
                    "link_token": token,
                    "application": _app_payload(personal={
                        "first": f"A{i}", "last": "Roe", "email": f"a{i}@x.com",
                        "phone": "555-1212", "city": "Austin", "state": "TX",
                        "dob": "1992-05-05", "ssn": f"111-22-333{i}",
                    }),
                }, files=_GOOD_FILES)

            page = await c.get("/api/applications?limit=2", headers=h)
            assert page.status_code == 200, page.text
            assert len(page.json()["items"]) == 2
            assert page.json()["total"] == 3     # the honest number


class TestLinkExpirySweep:
    """The nightly warning before a recruiting link dies — one-shot per
    link, so a scheduler retry cannot re-send."""

    async def test_sweep_finds_a_lapsing_link_once(self, api):
        from datetime import datetime, timedelta, timezone
        app, db = api
        acct = await db.create_account("Recruit Co")
        rec = await db.create_user(770004, acct.id, role=Role.RECRUITER)
        h = _recruiter_headers(rec, acct)
        now = datetime.now(timezone.utc)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            soon_id = (await c.post("/api/applications/links", headers=h,
                                    json={"label": "lapsing", "expires_in_days": 2})).json()["id"]
            await c.post("/api/applications/links", headers=h,
                         json={"label": "long", "expires_in_days": 90})

        window = dict(
            now_iso=now.isoformat(),
            before_iso=(now + timedelta(days=3)).isoformat(),
        )
        due = await db.list_links_expiring_soon(**window)
        assert [d["id"] for d in due] == [soon_id]   # the 90-day link is not due

        await db.mark_link_expiry_warned(soon_id)
        assert await db.list_links_expiring_soon(**window) == []

    async def test_revoked_links_are_never_warned_about(self, api):
        from datetime import datetime, timedelta, timezone
        app, db = api
        acct = await db.create_account("Recruit Co")
        rec = await db.create_user(770005, acct.id, role=Role.RECRUITER)
        h = _recruiter_headers(rec, acct)
        now = datetime.now(timezone.utc)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            lid = (await c.post("/api/applications/links", headers=h,
                                json={"label": "x", "expires_in_days": 1})).json()["id"]
            await c.post(f"/api/applications/links/{lid}/revoke", headers=h)

        due = await db.list_links_expiring_soon(
            now_iso=now.isoformat(),
            before_iso=(now + timedelta(days=3)).isoformat(),
        )
        assert all(d["id"] != lid for d in due)


class TestApplicantReceipt:
    """The applicant's own copy of their reference number — without it the
    self-service status page is unusable by the person it exists for."""

    def test_receipt_names_the_carrier_and_carries_the_reference(self, monkeypatch):
        sent: dict = {}
        import capabilities.email.application_emails as mod
        monkeypatch.setattr(mod, "is_email_configured", lambda: True)
        monkeypatch.setattr(mod, "send_email", lambda **kw: sent.update(kw) or True)

        assert mod.send_application_received_email(
            to="jane@x.com", applicant_name="Jane Roe",
            carrier_name="Premier Trucking", reference="APP-ABC123",
            status_url="https://apply.example/status/APP-ABC123",
        )
        assert "APP-ABC123" in sent["subject"]
        for field in ("body", "html_body"):
            assert "APP-ABC123" in sent[field]
            assert "Premier Trucking" in sent[field]
            assert "apply.example/status/APP-ABC123" in sent[field]
