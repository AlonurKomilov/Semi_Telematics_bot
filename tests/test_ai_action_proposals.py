"""AI write-action proposals — the storage spine + the execute
orchestration's security gates.

The storage tests run against the real Postgres fixture (claim atomicity
is the load-bearing anti-double-execute control).  The orchestration
tests use fakes to exercise the gate ladder fast.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import json

import pytest

from capabilities.permissions.roles import Role


async def _seed(pg_db, name="Actions Co"):
    from interfaces.api.auth import _hash_password
    acct = await pg_db.create_account(name)
    owner = await pg_db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id, role=Role.OWNER, display_name="Owner",
    )
    return acct.id, (owner.telegram_id or owner.id)


@pytest.fixture()
def _encryption_on(monkeypatch):
    from infra import crypto
    monkeypatch.setenv("ENCRYPTION_KEY", "test-passphrase-for-actions")
    crypto.init_encryption()
    yield
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    crypto.init_encryption()


class TestProposalStorage:
    async def test_create_get_roundtrip_encrypted(self, pg_db, _encryption_on):
        acct, uid = await _seed(pg_db)
        pid = await pg_db.create_action_proposal(
            acct, uid, "create_maintenance_task",
            "Create oil task for Truck 228",
            json.dumps({"vehicle_name": "228", "task_type": "oil"}),
            risk="low",
        )
        # Raw payload column is ciphertext, not the plaintext.
        cur = await pg_db._db.execute(
            "SELECT payload FROM ai_action_proposals WHERE id = ?", (pid,))
        raw = (await cur.fetchone())[0]
        assert raw.startswith("enc::") and "228" not in raw

        got = await pg_db.get_action_proposal(pid, acct, uid)
        assert got["tool"] == "create_maintenance_task"
        assert json.loads(got["payload"])["vehicle_name"] == "228"
        assert got["status"] == "pending"

    async def test_scoped_by_account_and_user(self, pg_db):
        acct, uid = await _seed(pg_db)
        pid = await pg_db.create_action_proposal(acct, uid, "t", "s", "{}")
        # Wrong user / wrong account → None (IDOR guard).
        assert await pg_db.get_action_proposal(pid, acct, uid + 1) is None
        assert await pg_db.get_action_proposal(pid, acct + 1, uid) is None
        assert await pg_db.get_action_proposal("nonexistent-uuid", acct, uid) is None

    async def test_claim_is_single_use(self, pg_db):
        acct, uid = await _seed(pg_db)
        pid = await pg_db.create_action_proposal(acct, uid, "t", "s", "{}")
        # First claim wins; the second sees no pending row.
        assert await pg_db.claim_action_proposal(pid, acct, uid) is True
        assert await pg_db.claim_action_proposal(pid, acct, uid) is False
        # And a foreign claim never succeeds.
        pid2 = await pg_db.create_action_proposal(acct, uid, "t", "s", "{}")
        assert await pg_db.claim_action_proposal(pid2, acct, uid + 1) is False

    async def test_expired_cannot_be_claimed(self, pg_db):
        acct, uid = await _seed(pg_db)
        pid = await pg_db.create_action_proposal(
            acct, uid, "t", "s", "{}", ttl_minutes=-1)   # already expired
        assert await pg_db.claim_action_proposal(pid, acct, uid) is False

    async def test_finalize_and_decline(self, pg_db):
        acct, uid = await _seed(pg_db)
        pid = await pg_db.create_action_proposal(acct, uid, "t", "s", "{}")
        await pg_db.claim_action_proposal(pid, acct, uid)
        await pg_db.finalize_action_proposal(
            acct and pid, acct, uid, "consumed", json.dumps({"created": True}))
        got = await pg_db.get_action_proposal(pid, acct, uid)
        assert got["status"] == "consumed"
        assert json.loads(got["result"])["created"] is True

        pid2 = await pg_db.create_action_proposal(acct, uid, "t", "s", "{}")
        assert await pg_db.decline_action_proposal(pid2, acct, uid) is True
        assert await pg_db.decline_action_proposal(pid2, acct, uid) is False  # already declined


class _FakePlatformDB:
    """In-memory proposal store mirroring the real claim semantics."""
    def __init__(self, prop):
        self._p = dict(prop)

    async def get_action_proposal(self, pid, account_id, user_id):
        p = self._p
        if p["id"] != pid or p["account_id"] != account_id or p["user_id"] != user_id:
            return None
        return dict(p)

    async def claim_action_proposal(self, pid, account_id, user_id):
        if (self._p["id"] == pid and self._p["status"] == "pending"
                and self._p["account_id"] == account_id and self._p["user_id"] == user_id):
            self._p["status"] = "executing"
            return True
        return False

    async def finalize_action_proposal(self, pid, account_id, user_id, status, result_json=""):
        self._p["status"] = status
        self._p["result"] = result_json


class _FakeTenantDB:
    def __init__(self): self.audits = []
    async def add_audit_log(self, account_id, user_id, action, target_type="", target_id="", details=""):
        self.audits.append({"action": action, "target_id": target_id})
        return 1


def _prop(**over):
    base = {"id": "p1", "account_id": 1, "user_id": 7,
            "tool": "create_maintenance_task", "summary": "Create task",
            "payload": json.dumps({"vehicle_name": "228", "task_type": "oil"}),
            "risk": "low", "status": "pending", "result": ""}
    base.update(over)
    return base


class TestExecuteOrchestration:
    @pytest.fixture(autouse=True)
    def _stub_executor(self, monkeypatch):
        """Register a fake executor + permit-all gate for the tests."""
        import capabilities.ai.actions as actions
        import capabilities.ai.tools as tools

        async def _exec(payload, account_id, user_context, db):
            return {"created": True, "target_type": "maintenance_task",
                    "target_id": "99", "message": "Created."}
        monkeypatch.setattr(tools, "get_action_executor",
                            lambda name: _exec if name == "create_maintenance_task" else None)
        monkeypatch.setattr(actions, "get_action_executor",
                            lambda name: _exec if name == "create_maintenance_task" else None)
        monkeypatch.setattr(tools, "get_tool_schema",
                            lambda name: {"writes": True, "risk": "low"} if name == "create_maintenance_task" else None)
        monkeypatch.setattr(actions, "get_tool_schema",
                            lambda name: {"writes": True, "risk": "low"} if name == "create_maintenance_task" else None)

        async def _permit(*a, **k):
            return None   # allowed
        import capabilities.ai.intelligence as intel
        monkeypatch.setattr(intel, "_check_tool_permission", _permit)

    async def _run(self, prop, role="owner"):
        from capabilities.ai.actions import execute_approved_action
        pdb = _FakePlatformDB(prop)
        tdb = _FakeTenantDB()
        user = {"account_id": prop["account_id"], "sub": str(prop["user_id"]), "role": role}
        res = await execute_approved_action(
            prop["id"], user=user, user_context={"role": role},
            platform_db=pdb, tenant_db=tdb)
        return res, pdb, tdb

    async def test_happy_path_executes_claims_and_audits(self):
        res, pdb, tdb = await self._run(_prop())
        assert res["status"] == "consumed"
        assert res["result"]["created"] is True
        assert pdb._p["status"] == "consumed"          # claimed + finalized
        assert tdb.audits[0]["action"] == "ai_write:create_maintenance_task"

    async def test_foreign_proposal_404(self):
        from fastapi import HTTPException
        from capabilities.ai.actions import execute_approved_action
        pdb = _FakePlatformDB(_prop(account_id=1, user_id=7))
        user = {"account_id": 1, "sub": "999", "role": "owner"}   # wrong user
        with pytest.raises(HTTPException) as ei:
            await execute_approved_action("p1", user=user, user_context={},
                                          platform_db=pdb, tenant_db=_FakeTenantDB())
        assert ei.value.status_code == 404

    async def test_already_consumed_returns_stored_result_idempotently(self):
        res, _, _ = await self._run(_prop(status="consumed",
                                          result=json.dumps({"created": True, "message": "x"})))
        assert res["status"] == "consumed" and res["result"]["created"] is True

    async def test_registry_gate_refuses_non_write(self, monkeypatch):
        import capabilities.ai.actions as actions
        monkeypatch.setattr(actions, "get_tool_schema", lambda name: {"writes": False})
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await self._run(_prop())
        assert ei.value.status_code == 400

    async def test_permission_block_403(self, monkeypatch):
        import capabilities.ai.intelligence as intel
        async def _deny(*a, **k):
            return {"error": "nope"}
        monkeypatch.setattr(intel, "_check_tool_permission", _deny)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await self._run(_prop())
        assert ei.value.status_code == 403

    async def test_high_risk_refused_while_disabled(self, monkeypatch):
        import capabilities.ai.actions as actions
        monkeypatch.setattr(actions, "get_tool_schema", lambda name: {"writes": True, "risk": "high"})
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            await self._run(_prop(risk="high"))
        assert ei.value.status_code == 403
