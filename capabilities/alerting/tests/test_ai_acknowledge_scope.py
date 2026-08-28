"""Vehicle-Access scope enforcement on the AI write path.

Covers the isolation break a code review found: a vehicle-scoped caller
(e.g. a driver) must NOT be able to acknowledge alerts on vehicles outside
their access, and must NOT create maintenance tasks on such vehicles.

Three layers, tested independently:
  1. Storage — the scope predicate lives in the same SQL that clears the
     row (authoritative, TOCTOU-free).
  2. Executor — resolves the approver's scope and passes it to storage.
  3. Gate — `_check_tool_permission` rejects an out-of-scope vehicle_name
     write at propose time, and fails closed for any unclassified write.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.roles import Role


async def _seed_account(pg_db):
    acct = await pg_db.create_account("Scope Co")
    return acct.id


async def _seed_alert(pg_db, account_id, vehicle_name, *, alert_type="fault"):
    """Create an active alert_history row; return its id."""
    row = await pg_db.upsert_alert_history(
        account_id, alert_type, f"vid-{vehicle_name}", vehicle_name,
        last_detail="check engine", severity="warning",
    )
    return int(row["id"])


# ── Layer 1: storage scope predicate ─────────────────────────────────
class TestStorageScope:
    async def test_out_of_scope_id_is_not_cleared(self, pg_db):
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "999")
        # Caller may only touch "228" — acking 999's alert must no-op.
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct, allowed_vehicle_names=["228"])
        assert got is None
        # And the row is still active (not cleared behind the scope check).
        vmap = await pg_db.get_alert_history_vehicles(acct, [aid])
        assert aid in vmap  # still present/active

    async def test_in_scope_id_is_cleared(self, pg_db):
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "999")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct, allowed_vehicle_names=["999"])
        assert got is not None and got["vehicle_name"] == "999"

    async def test_case_insensitive_match(self, pg_db):
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "Truck-Alpha")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct, allowed_vehicle_names=["truck-alpha"])
        assert got is not None

    async def test_empty_scope_clears_nothing(self, pg_db):
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "999")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct, allowed_vehicle_names=[])   # scoped to nothing
        assert got is None

    async def test_none_scope_is_unrestricted(self, pg_db):
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "999")
        got = await pg_db.acknowledge_alert_history(
            aid, 7, acct, allowed_vehicle_names=None)  # unrestricted
        assert got is not None


# ── Layer 2: executor defense-in-depth ───────────────────────────────
class TestExecutorScope:
    async def test_executor_skips_out_of_scope(self, pg_db):
        from capabilities.alerting.ai_tool import _execute_acknowledge_alerts
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "999")
        res = await _execute_acknowledge_alerts(
            {"alert_ids": [aid]}, acct,
            {"role": "driver", "scoped_vehicle_nums": ["228"]}, pg_db)
        assert res["acknowledged"] == 0 and res["requested"] == 1

    async def test_executor_acks_in_scope(self, pg_db):
        from capabilities.alerting.ai_tool import _execute_acknowledge_alerts
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "228")
        res = await _execute_acknowledge_alerts(
            {"alert_ids": [aid]}, acct,
            {"role": "driver", "scoped_vehicle_nums": ["228"]}, pg_db)
        assert res["acknowledged"] == 1


# ── Layer 2b: propose-time refusal ───────────────────────────────────
class TestProposeScope:
    async def test_propose_refuses_out_of_scope_ids(self, pg_db):
        from capabilities.alerting.ai_tool import acknowledge_alerts
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "999")
        # _scope_vehicles is what the orchestrator injects for a scoped caller.
        out = await acknowledge_alerts(
            {"alert_ids": [aid], "_scope_vehicles": ["228"]},
            None, account_id=acct, db=pg_db)
        assert out.get("error")   # refused, no proposal

    async def test_propose_allows_in_scope_ids(self, pg_db):
        from capabilities.alerting.ai_tool import acknowledge_alerts
        acct = await _seed_account(pg_db)
        aid = await _seed_alert(pg_db, acct, "228")
        out = await acknowledge_alerts(
            {"alert_ids": [aid], "_scope_vehicles": ["228"]},
            None, account_id=acct, db=pg_db)
        assert out.get("proposed") is True


# ── Layer 3: gate (no DB) ────────────────────────────────────────────
class TestGateScope:
    async def test_create_task_out_of_scope_blocked(self):
        from capabilities.ai.intelligence import _check_tool_permission
        # scoped_vehicle_nums in context makes the caller scoped regardless of
        # role; owner carries can_maintenance_all so we exercise the SCOPE
        # branch, not the permission branch.
        blocked = await _check_tool_permission(
            "create_maintenance_task", {"vehicle_name": "999"},
            "owner", {"scoped_vehicle_nums": ["228"]}, None)
        assert blocked is not None and "error" in blocked

    async def test_create_task_in_scope_allowed(self):
        from capabilities.ai.intelligence import _check_tool_permission
        allowed = await _check_tool_permission(
            "create_maintenance_task", {"vehicle_name": "228"},
            "owner", {"scoped_vehicle_nums": ["228"]}, None)
        assert allowed is None

    async def test_unclassified_write_fails_closed(self, monkeypatch):
        import capabilities.ai.intelligence as intel
        # A hypothetical write tool in NONE of the scope frozensets must be
        # denied for a scoped caller (the runtime backstop to the guard test).
        monkeypatch.setattr(
            intel, "get_tool_schema",
            lambda name: {"writes": True} if name == "phantom_write" else None)
        blocked = await intel._check_tool_permission(
            "phantom_write", {}, "owner", {"scoped_vehicle_nums": ["228"]}, None)
        assert blocked is not None and "error" in blocked
