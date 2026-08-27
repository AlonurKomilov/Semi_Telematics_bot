"""API-level tests for account lifecycle: suspend enforcement at login,
operator suspend/unsuspend endpoints, owner self-delete flow, purge.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")
os.environ.setdefault("JWT_SECRET", "test-secret-32-chars-or-more-please-aaaaaaaaaaaa")

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.storage import Role


@pytest_asyncio.fixture
async def api(pg_db, monkeypatch):
    monkeypatch.setenv("SYSTEM_OWNER_IDS", "12345")
    import infra.platform as _cp
    _cp._db = pg_db

    from interfaces.api.app import create_api
    app = create_api()

    from interfaces.api import deps as _deps
    async def _fake_operator():
        return {"telegram_id": 12345, "role": "system_owner", "account_id": 0}
    app.dependency_overrides[_deps.require_system_owner] = _fake_operator
    return app, pg_db


async def _seed_account_with_owner(db, name="Lifecycle Co"):
    from interfaces.api.auth import _hash_password
    acct = await db.create_account(name)
    owner = await db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id,
        role=Role.OWNER,
        display_name="Owner",
    )
    # Email-verified so login works.
    await db._db.execute(
        "UPDATE users SET email_verified = 1 WHERE id = ?", (owner.id,)
    )
    await db._db.commit()
    return acct, owner


def _owner_headers(owner, acct):
    from interfaces.api.auth import create_jwt
    token = create_jwt(owner.telegram_id or 0, acct.id, "owner", user_id=owner.id)
    return {"Authorization": f"Bearer {token}"}


class TestSuspendEnforcement:
    async def test_login_rejected_when_suspended(self, api):
        app, db = api
        acct, owner = await _seed_account_with_owner(db, "Suspended Co")
        await db.suspend_account(acct.id, reason="fraud", operator_tg_id=1)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/login", json={
                "email": owner.email, "password": "ownerpass123",
            })
        assert r.status_code == 403, r.text
        assert "account_suspended" in r.text

    async def test_operator_suspend_endpoint_full_cycle(self, api):
        app, db = api
        acct, _ = await _seed_account_with_owner(db, "Cycle Co")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post(f"/api/system/accounts/{acct.id}/suspend",
                             json={"reason": "test suspension"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "suspended"

            # Double-suspend → 409
            r2 = await c.post(f"/api/system/accounts/{acct.id}/suspend",
                              json={"reason": "again"})
            assert r2.status_code == 409

            # Lifecycle endpoint reflects the state
            r3 = await c.get(f"/api/system/accounts/{acct.id}/lifecycle")
            assert r3.json()["suspended_at"]
            assert r3.json()["suspended_reason"] == "test suspension"

            # Unsuspend restores
            r4 = await c.post(f"/api/system/accounts/{acct.id}/unsuspend")
            assert r4.status_code == 200
            r5 = await c.get(f"/api/system/accounts/{acct.id}/lifecycle")
            assert r5.json()["suspended_at"] is None
            assert r5.json()["is_active"] is True

        # Audit rows recorded
        rows = await db.list_platform_audit()
        events = [r["event"] for r in rows]
        assert "account_suspended" in events
        assert "account_unsuspended" in events


class TestOwnerDeleteFlow:
    async def test_full_request_confirm_cancel_cycle(self, api):
        app, db = api
        acct, owner = await _seed_account_with_owner(db, "Deleting Co")
        headers = _owner_headers(owner, acct)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            # Step 1: request → code minted (email send is best-effort)
            r = await c.post("/api/user/account/delete/request", headers=headers)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "code_sent"

            # Grab the code via the DB (email is stubbed out in tests)
            # by minting our own — remint invalidates the emailed one,
            # which is fine: we only care that confirm consumes it.
            code = await db.create_deletion_code(acct.id, owner.id)

            # Step 2: confirm with WRONG code → 400
            r2 = await c.post("/api/user/account/delete/confirm",
                              headers=headers, json={"code": "000000"})
            assert r2.status_code == 400

            # Confirm with the right code → scheduled
            r3 = await c.post("/api/user/account/delete/confirm",
                              headers=headers, json={"code": code})
            assert r3.status_code == 200, r3.text
            body = r3.json()
            assert body["status"] == "deletion_scheduled"
            assert body["purge_at"]
            assert body["grace_days"] == 90

            # Lifecycle reflects pending deletion
            r4 = await c.get("/api/user/account/lifecycle", headers=headers)
            assert r4.json()["deleted_at"]

            # Step 3: cancel → reactivated
            r5 = await c.post("/api/user/account/delete/cancel", headers=headers)
            assert r5.status_code == 200
            assert r5.json()["status"] == "reactivated"

            r6 = await c.get("/api/user/account/lifecycle", headers=headers)
            assert r6.json()["deleted_at"] is None
            assert r6.json()["is_active"] is True

        rows = await db.list_platform_audit()
        events = [r["event"] for r in rows]
        assert "account_deletion_requested" in events
        assert "account_deletion_cancelled" in events

    async def test_non_owner_cannot_delete(self, api):
        app, db = api
        acct, _ = await _seed_account_with_owner(db, "Fleet Guard Co")
        fleet = await db.create_user(99887766, acct.id, role=Role.FLEET)
        from interfaces.api.auth import create_jwt
        token = create_jwt(fleet.telegram_id, acct.id, "fleet", user_id=fleet.id)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/user/account/delete/request",
                             headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    async def test_owner_can_login_during_grace_others_cannot(self, api):
        """The role-aware gate: owner in, everyone else out."""
        app, db = api
        acct, owner = await _seed_account_with_owner(db, "Grace Co")
        from interfaces.api.auth import _hash_password
        fleet = await db.create_user_with_email(
            email=f"fleet.{acct.id}@example.com",
            password_hash=_hash_password("fleetpass123"),
            account_id=acct.id, role=Role.FLEET, display_name="Fleet",
        )
        await db._db.execute(
            "UPDATE users SET email_verified = 1 WHERE id = ?", (fleet.id,)
        )
        await db._db.commit()

        await db.request_account_deletion(acct.id, owner_tg_id=owner.id)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r_owner = await c.post("/api/auth/login", json={
                "email": owner.email, "password": "ownerpass123",
            })
            r_fleet = await c.post("/api/auth/login", json={
                "email": fleet.email, "password": "fleetpass123",
            })
        assert r_owner.status_code == 200, r_owner.text
        assert r_fleet.status_code == 403, r_fleet.text
        assert "account_deletion_pending" in r_fleet.text


class TestPurge:
    async def test_purge_account_data_removes_everything(self, pg_db):
        acct, owner = await _seed_account_with_owner(pg_db, "Purge Me Inc")
        await pg_db.add_company(acct.id, "PRG", "key", "Purge Co")

        deleted = await pg_db.purge_account_data(acct.id)
        assert deleted.get("accounts") == 1
        assert deleted.get("users", 0) >= 1
        assert deleted.get("companies", 0) >= 1
        # role_permissions were seeded at create — must be gone too
        assert deleted.get("role_permissions", 0) >= 1

        assert await pg_db.get_account(acct.id) is None
        # No leftovers in any account_id table
        cur = await pg_db._db.execute(
            "SELECT DISTINCT table_name FROM information_schema.columns "
            "WHERE column_name = 'account_id'"
        )
        for row in await cur.fetchall():
            t = dict(row)["table_name"]
            cur2 = await pg_db._db.execute(
                f"SELECT COUNT(*) FROM {t} WHERE account_id = ?", (acct.id,)
            )
            assert (await cur2.fetchone())[0] == 0, f"leftover rows in {t}"

    async def test_purge_warning_window_query(self, pg_db):
        acct, owner = await _seed_account_with_owner(pg_db, "Warn Me Inc")
        # 5-day grace → lands inside the 7-day warning window
        await pg_db.request_account_deletion(
            acct.id, owner_tg_id=owner.id, grace_days=5,
        )
        rows = await pg_db.list_accounts_purging_within(days=7)
        assert any(r["id"] == acct.id for r in rows)
        # but NOT pending purge yet
        from datetime import datetime, timezone
        now_iso = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        assert acct.id not in await pg_db.list_accounts_pending_purge(before_iso=now_iso)


class TestRevokeAccountSessions:
    async def test_revokes_all_users_sessions(self, api):
        app, db = api
        acct, owner = await _seed_account_with_owner(db, "Sessions Co")

        # Log in to create a session row
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            r = await c.post("/api/auth/login", json={
                "email": owner.email, "password": "ownerpass123",
            })
            assert r.status_code == 200

        revoked = await db.revoke_account_sessions(acct.id)
        assert len(revoked) >= 1
        assert all(row["jti"] for row in revoked)
        # Second call: nothing left to revoke
        assert await db.revoke_account_sessions(acct.id) == []
