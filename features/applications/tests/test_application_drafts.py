"""Tests for apply-form save & resume (server-side drafts).

Focus: the security contract — draft-secret write gating, the email second
factor on resume, recruiters never seeing draft bodies — plus submit-side
cleanup and the retention prune.
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")
os.environ.setdefault("OBJECT_STORE_BACKEND", "disk")
os.environ.setdefault("OBJECT_STORE_ROOT", "/tmp/drafts_test_store")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


@pytest_asyncio.fixture
async def api(pg_db):
    import infra.platform as cp
    cp._db = pg_db
    # conftest.py pre-sets ENCRYPTION_KEY="" (suite default: crypto off), so
    # FORCE a real key + re-init here — the encrypted-at-rest assertion below
    # is meaningless otherwise.  Teardown restores the suite default.
    from infra.crypto import init_encryption
    os.environ["ENCRYPTION_KEY"] = "test-key-32-chars-min-aaaaaaaaaaaaaaaaaaaa"
    init_encryption()
    from interfaces.api.app import create_api
    app = create_api()
    yield app, pg_db
    os.environ["ENCRYPTION_KEY"] = ""
    init_encryption()


async def _link(db, name="Draft Co"):
    acct = await db.create_account(name)
    link = await db.create_application_link(acct.id, label="Indeed")
    return acct, link


def _save_body(link, email="jane@x.com", secret=None, **over):
    base = {
        "link_token": link["token"], "email": email, "draft_secret": secret,
        "first_name": "Jane", "last_name": "Roe", "step": 2, "steps_total": 10,
        "data": {"personal": {"first": "Jane", "email": email}, "gate": {"cdl1yr": "yes"}},
    }
    base.update(over)
    return base


class TestDraftSaveAndResume:
    async def test_save_resume_round_trip(self, api):
        app, db = api
        _acct, link = await _link(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/api/applications/draft", json=_save_body(link))
            assert r.status_code == 200, r.text
            secret = r.json()["draft_secret"]
            assert secret

            # Update with the secret succeeds; the stored body is ENCRYPTED.
            r2 = await c.post("/api/applications/draft",
                              json=_save_body(link, secret=secret, step=4))
            assert r2.status_code == 200 and r2.json()["draft_secret"] == secret
            cur = await db._db.execute("SELECT data_encrypted, step FROM application_drafts")
            row = await cur.fetchone()
            assert row["step"] == 4
            assert "Jane" not in row["data_encrypted"]   # encrypted at rest

            # Resume needs token + the SAME email (second factor).
            cur = await db._db.execute("SELECT resume_token FROM application_drafts")
            rtoken = (await cur.fetchone())["resume_token"]
            bad = await c.post("/api/applications/draft/resume",
                               json={"resume_token": rtoken, "email": "other@x.com"})
            assert bad.status_code == 404
            good = await c.post("/api/applications/draft/resume",
                                json={"resume_token": rtoken, "email": "JANE@X.COM"})
            assert good.status_code == 200
            j = good.json()
            assert j["link_token"] == link["token"] and j["step"] == 4
            assert j["data"]["personal"]["first"] == "Jane"
            assert j["draft_secret"] == secret

    async def test_foreign_session_cannot_clobber(self, api):
        app, db = api
        _acct, link = await _link(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/api/applications/draft", json=_save_body(link))).status_code == 200
            # No secret / wrong secret → 403, and the stored draft is untouched.
            for secret in (None, "wrong-secret"):
                r = await c.post("/api/applications/draft",
                                 json=_save_body(link, secret=secret, first_name="Mallory"))
                assert r.status_code == 403, secret
            cur = await db._db.execute("SELECT first_name FROM application_drafts")
            assert (await cur.fetchone())["first_name"] == "Jane"

    async def test_bad_token_and_bad_email(self, api):
        app, db = api
        _acct, link = await _link(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            assert (await c.post("/api/applications/draft",
                                 json=_save_body({"token": "nope"}))).status_code == 404
            assert (await c.post("/api/applications/draft",
                                 json=_save_body(link, email="not-an-email"))).status_code == 422

    async def test_send_link_requires_ownership_and_never_wipes(self, api):
        app, db = api
        _acct, link = await _link(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            secret = (await c.post("/api/applications/draft", json=_save_body(link))).json()["draft_secret"]
            r = await c.post("/api/applications/draft/send-link",
                             json={"link_token": link["token"], "email": "jane@x.com",
                                   "draft_secret": "wrong"})
            assert r.status_code == 403
            # Correct secret: 200 (sent=false in tests — SMTP unconfigured) and
            # the draft body survives the ownership probe.
            r = await c.post("/api/applications/draft/send-link",
                             json={"link_token": link["token"], "email": "jane@x.com",
                                   "draft_secret": secret})
            assert r.status_code == 200
            cur = await db._db.execute("SELECT first_name, data_encrypted FROM application_drafts")
            row = await cur.fetchone()
            assert row["first_name"] == "Jane" and row["data_encrypted"]


class TestRecruiterDraftsView:
    async def test_list_masks_contact_and_hides_body(self, api):
        app, db = api
        acct, link = await _link(db)
        rec = await db.create_user(830001, acct.id, role=Role.RECRUITER)
        from interfaces.api.auth import create_jwt
        headers = {"Authorization": f"Bearer {create_jwt(830001, acct.id, 'recruiter', user_id=rec.id)}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/applications/draft", json=_save_body(link))
            r = await c.get("/api/applications/drafts", headers=headers)
            assert r.status_code == 200
            items = r.json()["items"]
            assert len(items) == 1
            it = items[0]
            assert it["email_masked"] == "j***@x.com"
            assert it["first_name"] == "Jane" and it["step"] == 2
            # The draft body / tokens must never reach the recruiter list.
            blob = json.dumps(it)
            assert "data" not in it and "resume_token" not in blob and "draft_secret" not in blob

    async def test_drafts_are_account_scoped(self, api):
        app, db = api
        _acct_a, link_a = await _link(db, "A Co")
        acct_b, _link_b = await _link(db, "B Co")
        rec_b = await db.create_user(830002, acct_b.id, role=Role.RECRUITER)
        from interfaces.api.auth import create_jwt
        headers_b = {"Authorization": f"Bearer {create_jwt(830002, acct_b.id, 'recruiter', user_id=rec_b.id)}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/applications/draft", json=_save_body(link_a))
            r = await c.get("/api/applications/drafts", headers=headers_b)
            assert r.status_code == 200 and r.json()["items"] == []

    async def test_remind_caps_at_one_per_20h(self, api):
        app, db = api
        acct, link = await _link(db)
        rec = await db.create_user(830003, acct.id, role=Role.RECRUITER)
        from interfaces.api.auth import create_jwt
        headers = {"Authorization": f"Bearer {create_jwt(830003, acct.id, 'recruiter', user_id=rec.id)}"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/applications/draft", json=_save_body(link))
            cur = await db._db.execute("SELECT id FROM application_drafts")
            did = (await cur.fetchone())["id"]
            # Simulate a reminder sent just now → second remind is throttled.
            await db.mark_draft_emailed(did, reminder=True)
            r = await c.post(f"/api/applications/drafts/{did}/remind", headers=headers)
            assert r.status_code == 429


class TestAutoReminder:
    """Per-link auto-remind policy: opt-in cadence + hard lifetime cap."""

    @staticmethod
    async def _age(db, *, updated_hours: float | None = None, reminded_hours: float | None = None):
        from datetime import datetime, timedelta, timezone
        if updated_hours is not None:
            ts = (datetime.now(timezone.utc) - timedelta(hours=updated_hours)).isoformat()
            await db._db.execute("UPDATE application_drafts SET updated_at = ?", (ts,))
        if reminded_hours is not None:
            ts = (datetime.now(timezone.utc) - timedelta(hours=reminded_hours)).isoformat()
            await db._db.execute("UPDATE application_drafts SET reminder_sent_at = ?", (ts,))
        await db._db.commit()

    async def test_off_by_default_never_due(self, api):
        _app, db = api
        _acct, link = await _link(db)
        await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="a@x.com",
            draft_secret=None, data_encrypted="x")
        await self._age(db, updated_hours=100)
        assert await db.list_drafts_needing_reminder() == []

    async def test_cadence_spacing_and_lifetime_cap(self, api):
        _app, db = api
        acct, link = await _link(db)
        await db.update_application_link(acct.id, link["id"],
                                         remind_every_hours=24, remind_max=2)
        await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="a@x.com",
            draft_secret=None, data_encrypted="x")
        # Fresh draft → not idle long enough.
        assert await db.list_drafts_needing_reminder() == []
        # Idle 25h → due; stamping (auto) bumps the counter.
        await self._age(db, updated_hours=25)
        due = await db.list_drafts_needing_reminder()
        assert len(due) == 1
        await db.mark_draft_emailed(due[0]["id"], reminder=True, auto=True)
        # Immediately after: spaced off the previous nudge → not due.
        assert await db.list_drafts_needing_reminder() == []
        # Another cadence passes → due again (reminder #2 = the cap).
        await self._age(db, updated_hours=50, reminded_hours=25)
        due = await db.list_drafts_needing_reminder()
        assert len(due) == 1
        await db.mark_draft_emailed(due[0]["id"], reminder=True, auto=True)
        # Cap reached → NEVER due again, no matter how long it idles.
        await self._age(db, updated_hours=500, reminded_hours=400)
        assert await db.list_drafts_needing_reminder() == []

    async def test_activity_resets_the_idle_clock(self, api):
        _app, db = api
        acct, link = await _link(db)
        await db.update_application_link(acct.id, link["id"], remind_every_hours=24)
        r = await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="a@x.com",
            draft_secret=None, data_encrypted="x")
        await self._age(db, updated_hours=30)
        assert len(await db.list_drafts_needing_reminder()) == 1
        # The applicant comes back (a save touches updated_at) → clock resets.
        await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="a@x.com",
            draft_secret=r["draft_secret"], data_encrypted="y")
        assert await db.list_drafts_needing_reminder() == []

    async def test_revoked_link_is_silent(self, api):
        _app, db = api
        acct, link = await _link(db)
        await db.update_application_link(acct.id, link["id"], remind_every_hours=24)
        await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="a@x.com",
            draft_secret=None, data_encrypted="x")
        await self._age(db, updated_hours=48)
        await db.set_application_link_active(acct.id, link["id"], False)
        assert await db.list_drafts_needing_reminder() == []

    async def test_job_sends_and_stamps(self, api, monkeypatch):
        _app, db = api
        acct, link = await _link(db)
        await db.update_application_link(acct.id, link["id"], remind_every_hours=24)
        await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="a@x.com",
            draft_secret=None, data_encrypted="x")
        await self._age(db, updated_hours=25)

        sent: list[dict] = []
        def fake_send(**kw):
            sent.append(kw)
            return True
        monkeypatch.setattr(
            "capabilities.email.application_emails.send_resume_link_email", fake_send)

        from features.applications.drafts_reminder import job_send_draft_reminders
        await job_send_draft_reminders()
        assert len(sent) == 1
        assert sent[0]["reminder"] is True and "/resume/" in sent[0]["resume_url"]
        cur = await db._db.execute(
            "SELECT reminders_sent, reminder_sent_at FROM application_drafts")
        row = await cur.fetchone()
        assert row["reminders_sent"] == 1 and row["reminder_sent_at"]
        # Second run straight away: spaced → nothing sent.
        await job_send_draft_reminders()
        assert len(sent) == 1


class TestDraftLifecycle:
    async def test_prune_removes_idle_drafts(self, api):
        _app, db = api
        _acct, link = await _link(db)
        await db.upsert_application_draft(
            link["account_id"], link_token=link["token"], email="old@x.com",
            draft_secret=None, data_encrypted="x",
        )
        await db._db.execute(
            "UPDATE application_drafts SET updated_at = datetime('now', '-30 days')")
        await db._db.commit()
        assert await db.prune_application_drafts(days_keep=14) == 1
        cur = await db._db.execute("SELECT COUNT(*) AS n FROM application_drafts")
        assert (await cur.fetchone())["n"] == 0
