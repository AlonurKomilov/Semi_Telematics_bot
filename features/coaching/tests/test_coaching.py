"""Auto Coaching unit + integration tests.

Covers:
  * Pure rule evaluation matrix (score_threshold, incident_count)
  * CoachingMixin CRUD round-trip on Database
  * service.create_rule + assign_manual + acknowledge lifecycle
  * coaching_enabled kill-switch (raises CoachingDisabledError)
  * engine.evaluate end-to-end with synthetic drivers + idempotency
  * driver perm cannot ack another driver's assignment
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

from adapters.storage import Database
from features.coaching import engine as coaching_engine
from features.coaching import service as coaching_service
from features.coaching.engine import (
    evaluate_incident_rule,
    evaluate_score_rule,
)
from features.coaching.models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    CoachingRule,
)
from features.coaching.service import CoachingDisabledError


ACCOUNT_ID = 1


# ── fixtures ─────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def tenant(pg_db):
    """Per-test PG database — same engine as production.

    Forwards to the shared ``pg_db`` fixture (testcontainers PG with
    per-test schema reset) so the original "construct a fresh tenant
    DB" intent is preserved without the legacy SQLite path.
    """
    yield pg_db


def _patch_services(monkeypatch, tenant_db, *, coaching_enabled: bool = True,
                    cards: list[dict] | None = None,
                    safety_events: list[dict] | None = None) -> None:
    async def _get_tenant(_aid):
        return tenant_db

    fake_account = SimpleNamespace(
        id=ACCOUNT_ID, name="Test", coaching_enabled=coaching_enabled,
    )

    class _FakePlatformDB:
        async def get_account(self, _aid):
            return fake_account

    fake_pdb = _FakePlatformDB()

    async def _evaluate_subjects(_aid, *, subject="driver", days=30,
                                 write_evidence=False):
        return cards or []

    async def _events(_aid, *, days=30, event_type=None, driver_id=None,
                      limit=2000):
        out = []
        for ev in (safety_events or []):
            if event_type and ev.get("event_type") != event_type:
                continue
            if driver_id and ev.get("driver_id") != driver_id:
                continue
            out.append(ev)
        return out

    async def _counts_grouped(_aid, *, days=30, event_types=None, driver_ids=None):
        out: dict[tuple[str, str], int] = {}
        ets = set(event_types or [])
        dids = set(driver_ids or [])
        for ev in (safety_events or []):
            et = ev.get("event_type") or ""
            did = ev.get("driver_id") or ""
            if ets and et not in ets:
                continue
            if dids and did not in dids:
                continue
            out[(did, et)] = out.get((did, et), 0) + 1
        return out

    monkeypatch.setattr(coaching_engine, "get_tenant_db", _get_tenant)
    monkeypatch.setattr(coaching_service, "get_tenant_db", _get_tenant)
    monkeypatch.setattr(coaching_service, "get_db", lambda: fake_pdb)
    monkeypatch.setattr(coaching_engine, "evaluate_subjects", _evaluate_subjects)
    tenant_db.get_safety_events_warehouse = _events  # type: ignore[assignment]
    tenant_db.get_safety_event_counts_grouped = _counts_grouped  # type: ignore[assignment]


# ── pure rule evaluation matrix ──────────────────────────────────

class TestRuleEvaluation:
    def _score_rule(self, score_max=70.0):
        return CoachingRule(
            id=1, name="low score", kind=KIND_SCORE_THRESHOLD,
            topic_key="distraction", score_max=score_max,
        )

    def _incident_rule(self, min_count=3, event_type="hard_brake"):
        return CoachingRule(
            id=2, name="brakes", kind=KIND_INCIDENT_COUNT,
            topic_key="hard_brake", event_type=event_type,
            min_count=min_count,
        )

    def test_score_below_threshold_triggers(self):
        r = evaluate_score_rule(self._score_rule(70.0), 60.0)
        assert r is not None
        assert r.topic_key == "distraction"

    def test_score_at_threshold_triggers(self):
        r = evaluate_score_rule(self._score_rule(70.0), 70.0)
        assert r is not None

    def test_score_above_threshold_skips(self):
        assert evaluate_score_rule(self._score_rule(70.0), 80.0) is None

    def test_score_none_skips(self):
        assert evaluate_score_rule(self._score_rule(70.0), None) is None

    def test_incident_at_min_triggers(self):
        r = evaluate_incident_rule(self._incident_rule(3), 3)
        assert r is not None

    def test_incident_above_min_triggers(self):
        r = evaluate_incident_rule(self._incident_rule(3), 5)
        assert r is not None

    def test_incident_below_min_skips(self):
        assert evaluate_incident_rule(self._incident_rule(3), 2) is None

    def test_score_rule_ignores_incident_eval(self):
        assert evaluate_incident_rule(self._score_rule(), 99) is None


# ── CRUD round-trip ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_topics_seed_default(tenant):
    n = await tenant.seed_default_coaching_topics(ACCOUNT_ID)
    assert n == 6
    rows = await tenant.list_coaching_topics(ACCOUNT_ID)
    keys = {r["key"] for r in rows}
    assert "hard_brake" in keys
    assert "speeding" in keys


@pytest.mark.asyncio
async def test_rule_crud_roundtrip(tenant):
    rid = await tenant.create_coaching_rule(
        ACCOUNT_ID, name="Brakes", kind=KIND_INCIDENT_COUNT,
        topic_key="hard_brake", event_type="hard_brake", min_count=3,
        severity="high",
    )
    assert rid > 0
    rules = await tenant.list_coaching_rules(ACCOUNT_ID)
    assert len(rules) == 1
    assert rules[0]["severity"] == "high"

    ok = await tenant.update_coaching_rule(
        ACCOUNT_ID, rid, severity="medium", active=False,
    )
    assert ok
    rules = await tenant.list_coaching_rules(ACCOUNT_ID, active_only=True)
    assert rules == []

    await tenant.delete_coaching_rule(ACCOUNT_ID, rid)
    assert (await tenant.list_coaching_rules(ACCOUNT_ID)) == []


@pytest.mark.asyncio
async def test_assignment_lifecycle(tenant):
    aid = await tenant.create_coaching_assignment(
        ACCOUNT_ID, driver_id="DRV1", topic_key="speeding",
        severity="medium", reason="manual",
    )
    a = await tenant.get_coaching_assignment(ACCOUNT_ID, aid)
    assert a["status"] == "pending"

    pending = await tenant.has_pending_coaching_assignment(
        ACCOUNT_ID, "DRV1", "speeding", 30,
    )
    assert pending is True

    await tenant.set_coaching_assignment_status(
        ACCOUNT_ID, aid, "acknowledged",
    )
    a = await tenant.get_coaching_assignment(ACCOUNT_ID, aid)
    assert a["status"] == "acknowledged"
    assert a["acknowledged_at"] is not None

    await tenant.add_coaching_acknowledgment(
        assignment_id=aid, driver_id="DRV1", note="ok",
    )
    acks = await tenant.get_coaching_acknowledgments(aid)
    assert len(acks) == 1


# ── service kill-switch ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_kill_switch_blocks_create_rule(monkeypatch, tenant):
    _patch_services(monkeypatch, tenant, coaching_enabled=False)
    with pytest.raises(CoachingDisabledError):
        await coaching_service.create_rule(
            ACCOUNT_ID, user_id=1, name="x", kind=KIND_SCORE_THRESHOLD,
            topic_key="speeding", score_max=70.0,
        )


@pytest.mark.asyncio
async def test_kill_switch_blocks_acknowledge(monkeypatch, tenant):
    _patch_services(monkeypatch, tenant, coaching_enabled=True)
    aid = await coaching_service.assign_manual(
        ACCOUNT_ID, user_id=1, driver_id="DRV1",
        topic_key="speeding", severity="medium",
    )
    # Disable mid-flight
    _patch_services(monkeypatch, tenant, coaching_enabled=False)
    with pytest.raises(CoachingDisabledError):
        await coaching_service.acknowledge(
            ACCOUNT_ID, aid, driver_id="DRV1", user_id=1,
        )


# ── service: driver cannot ack others' assignments ───────────────

@pytest.mark.asyncio
async def test_acknowledge_wrong_driver_returns_false(monkeypatch, tenant):
    _patch_services(monkeypatch, tenant)
    aid = await coaching_service.assign_manual(
        ACCOUNT_ID, user_id=1, driver_id="DRV1",
        topic_key="speeding", severity="medium",
    )
    ok = await coaching_service.acknowledge(
        ACCOUNT_ID, aid, driver_id="DRV2", user_id=1,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_acknowledge_already_acked_returns_false(monkeypatch, tenant):
    _patch_services(monkeypatch, tenant)
    aid = await coaching_service.assign_manual(
        ACCOUNT_ID, user_id=1, driver_id="DRV1",
        topic_key="speeding", severity="medium",
    )
    ok1 = await coaching_service.acknowledge(
        ACCOUNT_ID, aid, driver_id="DRV1", user_id=1,
    )
    assert ok1 is True
    ok2 = await coaching_service.acknowledge(
        ACCOUNT_ID, aid, driver_id="DRV1", user_id=1,
    )
    assert ok2 is False


# ── engine.evaluate end-to-end ──────────────────────────────────

@pytest.mark.asyncio
async def test_engine_evaluate_score_rule(monkeypatch, tenant):
    cards = [
        {"driver_id": "DRV1", "score": 60.0},
        {"driver_id": "DRV2", "score": 90.0},
    ]
    _patch_services(monkeypatch, tenant, cards=cards)
    await tenant.create_coaching_rule(
        ACCOUNT_ID, name="Low score", kind=KIND_SCORE_THRESHOLD,
        topic_key="distraction", score_max=70.0, severity="high",
    )
    proposals = await coaching_engine.evaluate(ACCOUNT_ID, days=7)
    assert len(proposals) == 1
    assert proposals[0].driver_id == "DRV1"
    assert proposals[0].topic_key == "distraction"


@pytest.mark.asyncio
async def test_engine_evaluate_incident_rule(monkeypatch, tenant):
    cards = [
        {"driver_id": "DRV1", "score": 90.0},
        {"driver_id": "DRV2", "score": 95.0},
    ]
    events = [
        {"driver_id": "DRV1", "event_type": "hard_brake"},
        {"driver_id": "DRV1", "event_type": "hard_brake"},
        {"driver_id": "DRV1", "event_type": "hard_brake"},
        {"driver_id": "DRV2", "event_type": "hard_brake"},
    ]
    _patch_services(monkeypatch, tenant, cards=cards, safety_events=events)
    await tenant.create_coaching_rule(
        ACCOUNT_ID, name="Brakes", kind=KIND_INCIDENT_COUNT,
        topic_key="hard_brake", event_type="hard_brake",
        min_count=3, severity="medium",
    )
    proposals = await coaching_engine.evaluate(ACCOUNT_ID, days=7)
    assert len(proposals) == 1
    assert proposals[0].driver_id == "DRV1"


@pytest.mark.asyncio
async def test_engine_idempotent_skips_existing_pending(monkeypatch, tenant):
    cards = [{"driver_id": "DRV1", "score": 50.0}]
    _patch_services(monkeypatch, tenant, cards=cards)
    await tenant.create_coaching_rule(
        ACCOUNT_ID, name="Low score", kind=KIND_SCORE_THRESHOLD,
        topic_key="distraction", score_max=70.0,
    )
    # First run creates, persist via service
    ids1 = await coaching_service.run_evaluation(ACCOUNT_ID, days=7)
    assert len(ids1) == 1
    # Second run should skip because pending exists
    ids2 = await coaching_service.run_evaluation(ACCOUNT_ID, days=7)
    assert ids2 == []


@pytest.mark.asyncio
async def test_engine_dedups_within_batch(monkeypatch, tenant):
    """Two rules targeting the same topic for the same driver — only one
    proposal should emerge."""
    cards = [{"driver_id": "DRV1", "score": 50.0}]
    _patch_services(monkeypatch, tenant, cards=cards)
    await tenant.create_coaching_rule(
        ACCOUNT_ID, name="A", kind=KIND_SCORE_THRESHOLD,
        topic_key="distraction", score_max=70.0,
    )
    await tenant.create_coaching_rule(
        ACCOUNT_ID, name="B", kind=KIND_SCORE_THRESHOLD,
        topic_key="distraction", score_max=80.0,
    )
    proposals = await coaching_engine.evaluate(ACCOUNT_ID, days=7)
    assert len(proposals) == 1
