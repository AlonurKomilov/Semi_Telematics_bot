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


class TestPublicUrlSafety:
    """`website` and `video_url` are writable by an UNAUTHENTICATED carrier
    and rendered as links in the authenticated dashboard, so a non-http(s)
    scheme must never survive the write."""

    def test_javascript_and_data_urls_are_dropped(self):
        from features.carrier_directory.router import _safe_url
        for evil in (
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)  ",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ):
            assert _safe_url(evil) == "", evil

    def test_http_urls_pass_and_bare_hosts_get_https(self):
        from features.carrier_directory.router import _safe_url
        assert _safe_url("https://acme.example/x") == "https://acme.example/x"
        assert _safe_url("http://acme.example") == "http://acme.example"
        assert _safe_url("acme.example") == "https://acme.example"
        assert _safe_url("") == ""
        assert _safe_url("   ") == ""

    async def test_carrier_cannot_store_a_javascript_url(self, api):
        """End-to-end: the public submit path must sanitise, not just the
        authenticated one."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810090, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            token = (await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                                  json={})).json()["token"]
            r = await c.post("/api/carrier-directory/intake", json={
                "token": token,
                "website": "javascript:alert('xss')",
                "video_url": "javascript:alert('xss')",
                "content": {},
            })
            assert r.status_code == 200, r.text
            got = (await c.get(f"{BASE}/{cid}", headers=h)).json()
            assert got["website"] == ""
            assert got["video_url"] == ""
            assert "javascript:" not in (await c.get(f"{BASE}/{cid}", headers=h)).text


class TestFieldTemplateDrift:
    """The backend states the form's size in the invite email but the
    template lives in the dashboard's TypeScript.  This is the seam."""

    def test_public_field_count_matches_template(self):
        import re
        from pathlib import Path
        from features.carrier_directory.service import (
            PUBLIC_BASIC_FIELDS, PUBLIC_FIELD_COUNT,
        )
        src = Path(__file__).resolve().parents[1] / (
            "interfaces/dashboard/src/features/carrier-directory/fields.ts"
        )
        text = src.read_text()
        body = text[text.index("export const SECTIONS"):text.index("export const PUBLIC_SECTIONS")]
        rows = 0
        for block in re.split(r"\n  \{\n    key: '", body)[1:]:
            key = block[:block.index("'")]
            if key in ("prequal", "presentation"):     # the carrier-visible ones
                rows += len(re.findall(r"\n      f\(", block))
        assert rows + PUBLIC_BASIC_FIELDS == PUBLIC_FIELD_COUNT, (
            f"fields.ts now has {rows} public rows + {PUBLIC_BASIC_FIELDS} basics "
            f"= {rows + PUBLIC_BASIC_FIELDS}, but service.PUBLIC_FIELD_COUNT says "
            f"{PUBLIC_FIELD_COUNT}. Update the constant — the invite email quotes it."
        )

    def test_internal_section_is_not_carrier_writable(self):
        """fields.ts marks recruiter_only `internal`; the router's allow-list
        is the independent enforcement. They must agree."""
        from pathlib import Path
        from features.carrier_directory.router import (
            _INTAKE_ROW_SECTIONS, _INTAKE_TEXT_SECTIONS,
        )
        src = Path(__file__).resolve().parents[1] / (
            "interfaces/dashboard/src/features/carrier-directory/fields.ts"
        )
        text = src.read_text()
        assert "internal: true" in text
        # Whatever the frontend calls internal must be absent from both
        # server-side write allow-lists.
        assert "recruiter_only" not in _INTAKE_ROW_SECTIONS
        assert "recruiter_only" not in _INTAKE_TEXT_SECTIONS


class TestSenderNameResolver:
    """Unit-level: the one function every sender-name surface routes
    through.  Guards the precedence and the no-fallback-to-registered-name
    rule without needing the DB."""

    def _acct(self, **kw):
        from adapters.storage.models import Account
        base = dict(id=1, name="Premier Trucking Group Inc", slug="ptg",
                    tier="free", is_active=True, created_at="")
        base.update(kw)
        return Account(**base)

    def test_override_wins(self):
        from features.carrier_directory.service import resolve_sender_name
        acct = self._acct(public_display_name="Acme Driver Services")
        got = resolve_sender_name({"intake_display_name": "Northside"}, acct)
        assert got == "Northside"

    def test_falls_back_to_account_then_to_blank(self):
        from features.carrier_directory.service import resolve_sender_name
        acct = self._acct(public_display_name="Acme Driver Services")
        assert resolve_sender_name({}, acct) == "Acme Driver Services"
        assert resolve_sender_name({}, self._acct()) == ""

    def test_never_returns_the_registered_name(self):
        from features.carrier_directory.service import resolve_sender_name
        # Nothing configured anywhere, and a missing account object —
        # both must stay blank rather than reaching for accounts.name.
        assert resolve_sender_name({}, self._acct()) == ""
        assert resolve_sender_name({}, None) == ""
        assert resolve_sender_name({"intake_display_name": "   "}, None) == ""

    def test_whitespace_and_length_are_bounded(self):
        from features.carrier_directory.service import (
            MAX_SENDER_NAME, resolve_sender_name,
        )
        assert resolve_sender_name({"intake_display_name": "  Acme  "}, None) == "Acme"
        long = "A" * (MAX_SENDER_NAME + 50)
        assert len(resolve_sender_name({"intake_display_name": long}, None)) == MAX_SENDER_NAME

    def test_control_characters_cannot_reach_a_mail_header(self):
        """This name lands in an SMTP Subject, so CR/LF must not survive
        the resolver — ``send_email`` strips them too, but the choke
        point shouldn't rely on the transport remembering."""
        from features.carrier_directory.service import resolve_sender_name
        evil = "Acme\r\nBcc: attacker@evil.example"
        got = resolve_sender_name({"intake_display_name": evil}, None)
        assert "\r" not in got and "\n" not in got
        assert got == "AcmeBcc: attacker@evil.example"
        # A name made only of control characters is empty, not whitespace.
        assert resolve_sender_name({"intake_display_name": "\r\n\t"}, None) == ""


class TestIntakeInviteEmail:
    """The invite email is the SECOND surface that names the sender to an
    outsider.  It must speak with exactly the same voice as the page —
    including saying nothing when the sender chose to stay unnamed."""

    def _capture(self, monkeypatch):
        sent: dict = {}

        def fake_send(**kw):
            sent.update(kw)
            return True

        import capabilities.email.application_emails as mod
        monkeypatch.setattr(mod, "is_email_configured", lambda: True)
        monkeypatch.setattr(mod, "send_email", fake_send)
        return sent

    def test_named_sender_appears(self, monkeypatch):
        from capabilities.email.application_emails import send_carrier_intake_email
        sent = self._capture(monkeypatch)
        assert send_carrier_intake_email(
            to="ops@carrier.example", carrier_name="Expedited Trucking LLC",
            agency_name="Acme Driver Services",
            intake_url="https://apply.example/carrier/tok", expires_days=30,
        )
        assert "Acme Driver Services" in sent["subject"]
        assert "Acme Driver Services" in sent["body"]
        assert "Acme Driver Services" in sent["html_body"]

    def test_blank_sender_names_nobody(self, monkeypatch):
        """A blank sender must NOT be back-filled with the product brand
        (the old ``_company_name()`` fallback) — the clause is dropped."""
        from capabilities.email.application_emails import send_carrier_intake_email
        sent = self._capture(monkeypatch)
        assert send_carrier_intake_email(
            to="ops@carrier.example", carrier_name="Expedited Trucking LLC",
            agency_name="", intake_url="https://apply.example/carrier/tok",
            expires_days=30,
        )
        assert sent["subject"] == "Complete your carrier profile"
        # The product brand may still appear as the mail shell's footer
        # ("— the 4truck team") — that's platform chrome telling the
        # recipient what sent the mail.  What must NOT happen is the
        # product standing in for the SENDER, which is what the old
        # ``_company_name()`` fallback did in the subject and lede.
        assert "4truck" not in sent["subject"].lower()
        assert "4truck" not in sent["body"].lower()
        # The carrier still learns what the form is for and who it's about.
        assert "Expedited Trucking LLC" in sent["body"]
        assert "recruiting partner" in sent["body"]


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

    async def test_registered_account_name_never_reaches_the_carrier(self, api):
        """The core privacy contract: an external carrier holding a valid
        token must not be able to read the tenant's registered name.

        Before the sender-identity work the public GET echoed
        ``accounts.name`` unconditionally, so this asserts on the raw
        response body — not just the parsed field — to catch it leaking
        back in through any new key."""
        app, db = api
        acct = await db.create_account("Premier Trucking Group Inc")
        mgr = await db.create_user(810070, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            token = (await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                                  json={})).json()["token"]

            pub = await c.get(f"/api/carrier-directory/intake?token={token}")
            assert pub.status_code == 200, pub.text
            assert "Premier Trucking Group Inc" not in pub.text
            # Nothing configured → a blank sender, which the page renders
            # as neutral wording.  Blank is the answer, not a missing value.
            assert pub.json()["agency"] == ""

    async def test_sender_name_precedence(self, api):
        """Per-link override beats the account-wide name, which beats
        nothing at all — and neither path can fall through to
        ``accounts.name``."""
        app, db = api
        acct = await db.create_account("Premier Trucking Group Inc")
        mgr = await db.create_user(810071, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]

            # 1. Account-wide name only.
            await db.update_account(acct.id, public_display_name="Acme Driver Services")
            token = (await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                                  json={})).json()["token"]
            pub = await c.get(f"/api/carrier-directory/intake?token={token}")
            assert pub.json()["agency"] == "Acme Driver Services"

            # 2. Per-link override wins over it.
            token2 = (await c.post(
                f"{BASE}/{cid}/intake-link", headers=h,
                json={"display_name": "Northside Recruiting"},
            )).json()["token"]
            pub2 = await c.get(f"/api/carrier-directory/intake?token={token2}")
            assert pub2.json()["agency"] == "Northside Recruiting"
            assert "Premier Trucking Group Inc" not in pub2.text

            # 3. Clearing the account-wide name with no override → nobody
            #    named, NOT a fallback to the registered name.
            await db.update_account(acct.id, public_display_name="")
            token3 = (await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                                   json={})).json()["token"]
            pub3 = await c.get(f"/api/carrier-directory/intake?token={token3}")
            assert pub3.json()["agency"] == ""
            assert "Premier Trucking Group Inc" not in pub3.text

    async def test_patch_edits_link_without_rotating_the_token(self, api):
        """Editing the sender name must not invalidate a link the carrier
        already has — that was the whole reason for adding PATCH."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810072, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            token = (await c.post(
                f"{BASE}/{cid}/intake-link", headers=h,
                json={"display_name": "Typo Recruting"},
            )).json()["token"]

            r = await c.patch(f"{BASE}/{cid}/intake-link", headers=h,
                              json={"display_name": "Typo Recruiting"})
            assert r.status_code == 200, r.text
            assert r.json()["display_name"] == "Typo Recruiting"

            # SAME token still resolves, now with the corrected name.
            pub = await c.get(f"/api/carrier-directory/intake?token={token}")
            assert pub.status_code == 200
            assert pub.json()["agency"] == "Typo Recruiting"

            # Clearing the override falls back to neutral, not to the name.
            await c.patch(f"{BASE}/{cid}/intake-link", headers=h,
                          json={"display_name": ""})
            pub2 = await c.get(f"/api/carrier-directory/intake?token={token}")
            assert pub2.json()["agency"] == ""

    async def test_patch_requires_a_live_link_and_manager_rights(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810073, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810074, acct.id, role=Role.RECRUITER)
        hm = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=hm, json=_SAMPLE)).json()["id"]

            # No link minted yet → nothing to edit.
            assert (await c.patch(f"{BASE}/{cid}/intake-link", headers=hm,
                                  json={"display_name": "x"})).status_code == 404

            token = (await c.post(f"{BASE}/{cid}/intake-link", headers=hm,
                                  json={"display_name": "Acme"})).json()["token"]
            # Plain recruiter cannot edit a link.
            assert (await c.patch(f"{BASE}/{cid}/intake-link", headers=hr,
                                  json={"display_name": "x"})).status_code == 403
            # Empty body changes nothing → 422 rather than a silent no-op.
            assert (await c.patch(f"{BASE}/{cid}/intake-link", headers=hm,
                                  json={})).status_code == 422

            # Revoking clears the per-link name, so a re-mint starts clean.
            await c.delete(f"{BASE}/{cid}/intake-link", headers=hm)
            assert (await c.get(f"/api/carrier-directory/intake?token={token}")).status_code == 404
            assert (await c.patch(f"{BASE}/{cid}/intake-link", headers=hm,
                                  json={"display_name": "x"})).status_code == 404
            row = await db.get_carrier_profile(acct.id, cid)
            assert row["intake_display_name"] == ""

    async def test_sender_identity_hint_is_manager_only(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810075, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810076, acct.id, role=Role.RECRUITER)
        hm = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        await db.update_account(acct.id, public_display_name="Acme Driver Services")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/api/carrier-directory/sender-identity", headers=hm)
            assert r.status_code == 200
            assert r.json()["account_display_name"] == "Acme Driver Services"
            # Read-only recruiters don't mint links, so they don't get it.
            assert (await c.get("/api/carrier-directory/sender-identity",
                                headers=hr)).status_code == 403

    async def test_bulk_actions_enforce_the_single_action_rules(self, api):
        """Bulk must never be a way to do something the single endpoint
        would refuse, and a partial result must be reported honestly."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810100, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810101, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            submitted = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            untouched = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]

            tok = (await c.post(f"{BASE}/{submitted}/intake-link", headers=h,
                                json={})).json()["token"]
            await c.post("/api/carrier-directory/intake", json={
                "token": tok,
                "content": {"prequal": [{"label": "Minimum Age", "value": "23"}]},
            })

            # Read-only recruiters can't run either bulk action.
            for path in ("bulk-mark-reviewed", "bulk-revoke-link"):
                assert (await c.post(f"{BASE}/{path}", headers=hr,
                                     json={"ids": [submitted]})).status_code == 403

            # Only the carrier with a pending review is actioned; the other
            # is skipped WITH A REASON rather than silently counted.
            r = await c.post(f"{BASE}/bulk-mark-reviewed", headers=h,
                             json={"ids": [submitted, untouched]})
            assert r.status_code == 200, r.text
            assert r.json()["updated"] == 1
            assert r.json()["updated_ids"] == [submitted]
            assert [s["reason"] for s in r.json()["skipped"]] == ["nothing to review"]
            assert not bool((await c.get(f"{BASE}/{submitted}", headers=h)).json()["intake_review_pending"])

            # Revoke: the live link goes, the one that never had a link is skipped.
            r2 = await c.post(f"{BASE}/bulk-revoke-link", headers=h,
                              json={"ids": [submitted, untouched]})
            assert r2.json()["updated"] == 1
            assert [s["reason"] for s in r2.json()["skipped"]] == ["no active link"]
            assert (await c.get(f"/api/carrier-directory/intake?token={tok}")).status_code == 404
            # The carrier's submitted answers survive the revoke — killing
            # the URL must not take the data with it.
            kept = (await c.get(f"{BASE}/{submitted}", headers=h)).json()
            assert kept["content"]["prequal"][0]["value"] == "23"

    async def test_bulk_cannot_reach_another_account(self, api):
        """Ids are not trusted — every row is re-fetched account-scoped."""
        app, db = api
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        mgr_a = await db.create_user(810102, acct_a.id, role=Role.RECRUITER)
        mgr_b = await db.create_user(810103, acct_b.id, role=Role.RECRUITER)
        ha = _headers(mgr_a, acct_a, "recruiter", is_manager=True)
        hb = _headers(mgr_b, acct_b, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=ha, json=_SAMPLE)).json()["id"]
            await c.post(f"{BASE}/{cid}/intake-link", headers=ha, json={})

            r = await c.post(f"{BASE}/bulk-revoke-link", headers=hb, json={"ids": [cid]})
            assert r.status_code == 200
            assert r.json()["updated"] == 0
            assert r.json()["skipped"][0]["reason"] == "not found"
            # A's link is untouched.
            row = await db.get_carrier_profile(acct_a.id, cid)
            assert row["intake_token"] != ""

            # Empty and oversized selections are rejected by the model.
            assert (await c.post(f"{BASE}/bulk-revoke-link", headers=ha,
                                 json={"ids": []})).status_code == 422
            assert (await c.post(f"{BASE}/bulk-revoke-link", headers=ha,
                                 json={"ids": list(range(300))})).status_code == 422

    async def test_new_endpoints_are_tenant_scoped(self, api):
        """PATCH intake-link and GET sender-identity are new surfaces on a
        security-sensitive feature — pin their scoping explicitly rather
        than inheriting confidence from the sibling routes."""
        app, db = api
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        mgr_a = await db.create_user(810080, acct_a.id, role=Role.RECRUITER)
        mgr_b = await db.create_user(810081, acct_b.id, role=Role.RECRUITER)
        ha = _headers(mgr_a, acct_a, "recruiter", is_manager=True)
        hb = _headers(mgr_b, acct_b, "recruiter", is_manager=True)
        await db.update_account(acct_a.id, public_display_name="A Public")
        await db.update_account(acct_b.id, public_display_name="B Public")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=ha, json=_SAMPLE)).json()["id"]
            await c.post(f"{BASE}/{cid}/intake-link", headers=ha, json={})

            # B cannot edit A's link — out of scope reads as "not found".
            assert (await c.patch(f"{BASE}/{cid}/intake-link", headers=hb,
                                  json={"display_name": "hijack"})).status_code == 404
            # …and A's link is untouched.
            row = await db.get_carrier_profile(acct_a.id, cid)
            assert row["intake_display_name"] == ""

            # Each account's hint reflects only its own name.
            a_hint = (await c.get("/api/carrier-directory/sender-identity", headers=ha)).json()
            b_hint = (await c.get("/api/carrier-directory/sender-identity", headers=hb)).json()
            assert a_hint["account_display_name"] == "A Public"
            assert b_hint["account_display_name"] == "B Public"

    async def test_patch_404s_when_the_link_dies_under_it(self, api):
        """The router's up-front check can go stale: a revoke landing
        between it and the UPDATE must surface as 404, not a success
        response describing a dead link."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810082, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            await c.post(f"{BASE}/{cid}/intake-link", headers=h, json={})
            # Simulate the race by revoking directly in storage, so the
            # router's cached row still shows a live token.
            await db.revoke_carrier_intake(acct.id, cid)
            r = await c.patch(f"{BASE}/{cid}/intake-link", headers=h,
                              json={"display_name": "too late"})
            assert r.status_code == 404, r.text

    async def test_mark_reviewed_clears_flag_without_editing(self, api):
        """Finding a submission correct shouldn't require a no-op save that
        bumps updated_at and claims an edit that never happened."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810091, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810092, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            token = (await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                                  json={})).json()["token"]
            await c.post("/api/carrier-directory/intake",
                         json={"token": token, "content": {}})
            before = (await c.get(f"{BASE}/{cid}", headers=h)).json()
            assert bool(before["intake_review_pending"])

            # Read-only recruiters can't clear it.
            assert (await c.post(f"{BASE}/{cid}/mark-reviewed",
                                 headers=hr)).status_code == 403

            r = await c.post(f"{BASE}/{cid}/mark-reviewed", headers=h)
            assert r.status_code == 200, r.text
            after = (await c.get(f"{BASE}/{cid}", headers=h)).json()
            assert not bool(after["intake_review_pending"])
            # The content is untouched — this is not a disguised save.
            assert after["content"] == before["content"]
            assert after["updated_at"] == before["updated_at"]
            assert (await c.post(f"{BASE}/9999/mark-reviewed", headers=h)).status_code == 404

    async def test_email_sent_stamp_only_on_confirmed_send(self, api):
        """`intake_email` records what a manager typed; only a real relay
        hand-off may claim the carrier was contacted."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810093, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            # SMTP is unconfigured in tests, so the send returns False.
            r = await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                             json={"email": "ops@carrier.example"})
            assert r.status_code == 200
            assert r.json()["emailed"] is False
            assert r.json()["email_requested"] is True
            row = await db.get_carrier_profile(acct.id, cid)
            assert row["intake_email"] == "ops@carrier.example"
            assert row["intake_email_sent_at"] == ""

    async def test_replacing_a_link_keeps_what_the_caller_sends(self, api):
        """set_carrier_intake rewrites every intake column, so the mint form
        must resend the sender name — and a re-mint must reset the sweep
        markers so a fresh link gets its own reminder cycle."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810094, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                         json={"display_name": "Acme Driver Services"})
            await db.stamp_intake_marker(cid, "intake_reminded_at")
            assert (await db.get_carrier_profile(acct.id, cid))["intake_reminded_at"]

            await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                         json={"display_name": "Acme Driver Services"})
            row = await db.get_carrier_profile(acct.id, cid)
            assert row["intake_display_name"] == "Acme Driver Services"
            assert row["intake_reminded_at"] == ""      # fresh cycle
            assert row["intake_expiry_warned_at"] == ""

    async def test_lifecycle_sweep_is_one_shot_per_link(self, api):
        """Both passes must be idempotent — a scheduler retry cannot
        double-send to a carrier."""
        from datetime import datetime, timedelta, timezone
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810095, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        now = datetime.now(timezone.utc)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            cid = (await c.post(BASE, headers=h, json=_SAMPLE)).json()["id"]
            await c.post(f"{BASE}/{cid}/intake-link", headers=h,
                         json={"email": "ops@carrier.example", "expires_in_days": 30})
        # Simulate an invite that really went out 10 days ago.
        await db.mark_carrier_intake_emailed(cid)
        await db._db.execute(
            "UPDATE carrier_profile SET intake_email_sent_at = ? WHERE id = ?",
            ((now - timedelta(days=10)).isoformat(), cid),
        )
        await db._db.commit()

        due = await db.list_intakes_needing_reminder(
            now_iso=now.isoformat(),
            older_than_iso=(now - timedelta(days=7)).isoformat(),
        )
        assert any(d["id"] == cid for d in due)

        await db.stamp_intake_marker(cid, "intake_reminded_at")
        again = await db.list_intakes_needing_reminder(
            now_iso=now.isoformat(),
            older_than_iso=(now - timedelta(days=7)).isoformat(),
        )
        assert all(d["id"] != cid for d in again)

        # A submitted sheet drops out of the expiry warning entirely.
        soon = await db.list_intakes_expiring_soon(
            now_iso=now.isoformat(),
            before_iso=(now + timedelta(days=90)).isoformat(),
        )
        assert any(s["id"] == cid for s in soon)
        await db.submit_carrier_intake(
            cid, website="", video_url="", experience_summary="", content="{}",
        )
        after = await db.list_intakes_expiring_soon(
            now_iso=now.isoformat(),
            before_iso=(now + timedelta(days=90)).isoformat(),
        )
        assert all(s["id"] != cid for s in after)

    async def test_stamp_intake_marker_rejects_arbitrary_columns(self, api):
        """The one interpolated identifier in this mixin's SQL."""
        import pytest
        _, db = api
        with pytest.raises(ValueError):
            await db.stamp_intake_marker(1, "intake_token")

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


class TestFieldColumnsPayload:
    """?fields=1 backs the directory grid's optional field columns — the
    thing that lets a recruiter screen the whole book against one driver
    instead of opening carriers one at a time."""

    async def test_content_is_opt_in(self, api):
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810110, acct.id, role=Role.RECRUITER)
        h = _headers(mgr, acct, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(BASE, headers=h, json=_SAMPLE)

            lean = (await c.get(BASE, headers=h)).json()["items"][0]
            assert "content" not in lean      # default list stays small

            rich = (await c.get(f"{BASE}?fields=1", headers=h)).json()["items"][0]
            assert rich["content"]["prequal"][0]["value"] == "At least 23 years of age"

    async def test_recruiter_only_never_ships_in_the_bulk_list(self, api):
        """A read-only recruiter must not receive the agency's internal
        playbook in bulk any more than they receive it on the detail page."""
        app, db = api
        acct = await db.create_account("Recruit Co")
        mgr = await db.create_user(810111, acct.id, role=Role.RECRUITER)
        rec = await db.create_user(810112, acct.id, role=Role.RECRUITER)
        hm = _headers(mgr, acct, "recruiter", is_manager=True)
        hr = _headers(rec, acct, "recruiter")
        sample = dict(_SAMPLE)
        sample["content"] = {
            **_SAMPLE["content"],
            "recruiter_only": [{"label": "Application Ownership", "value": "Exclusive 30 days"}],
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(BASE, headers=hm, json=sample)

            plain = await c.get(f"{BASE}?fields=1", headers=hr)
            assert plain.status_code == 200
            assert "recruiter_only" not in plain.json()["items"][0]["content"]
            assert "Exclusive 30 days" not in plain.text

            # A manager still gets it — it's their own playbook.
            mine = (await c.get(f"{BASE}?fields=1", headers=hm)).json()["items"][0]
            assert mine["content"]["recruiter_only"][0]["value"] == "Exclusive 30 days"

    async def test_bulk_list_is_account_scoped(self, api):
        app, db = api
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        mgr_a = await db.create_user(810113, acct_a.id, role=Role.RECRUITER)
        mgr_b = await db.create_user(810114, acct_b.id, role=Role.RECRUITER)
        ha = _headers(mgr_a, acct_a, "recruiter", is_manager=True)
        hb = _headers(mgr_b, acct_b, "recruiter", is_manager=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(BASE, headers=ha, json=_SAMPLE)
            assert (await c.get(f"{BASE}?fields=1", headers=hb)).json()["items"] == []
