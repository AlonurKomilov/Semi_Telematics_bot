"""Pay-for-Performance unit + integration tests.

Covers:
  * Pure rule evaluation matrix (score_threshold, incident_count)
  * DriverPayMixin CRUD round-trip on Database
  * service.create_run / finalize_run lifecycle
  * payroll_enabled kill-switch (raises DriverPayDisabledError)
  * compute_run end-to-end with a synthetic 3-driver fleet + 2 rules
"""

from __future__ import annotations

import os
import sys
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Database, Role
from features.driver_pay import engine as payroll_engine
from features.driver_pay import service as payroll_service
from features.driver_pay.engine import (
    compute_run,
    evaluate_incident_rule,
    evaluate_score_rule,
)
from features.driver_pay.models import (
    KIND_INCIDENT_COUNT,
    KIND_SCORE_THRESHOLD,
    STATUS_DRAFT,
    STATUS_FINALIZED,
    BonusRule,
)
from features.driver_pay.service import DriverPayDisabledError


ACCOUNT_ID = 1


# ── fixtures ─────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def tenant(pg_db):
    """Per-test PG database — same engine as production.

    Forwards to the shared ``pg_db`` fixture (testcontainers PG with
    per-test schema reset).
    """
    yield pg_db


def _patch_services(monkeypatch, tenant_db, *, payroll_enabled: bool = True,
                    cards: list[dict] | None = None,
                    safety_events: list[dict] | None = None,
                    loads: list[dict] | None = None,
                    off_items: list[dict] | None = None,
                    pay_items: list[dict] | None = None) -> None:
    """Wire the payroll engine + service to use our test tenant DB."""

    async def _get_tenant(_aid):
        return tenant_db

    fake_account = SimpleNamespace(
        id=ACCOUNT_ID,
        name="Test",
        # The service now reads the MODULE mask, not the legacy flag —
        # the harness keeps its boolean param and maps it.
        # Payroll is an Accounting feature now — disabling it = disabling
        # the accounting module (the per-user gate is can_driver_pay_admin).
        disabled_modules="" if payroll_enabled else "accounting",
    )

    class _FakePlatformDB:
        async def get_account(self, _aid):
            return fake_account

    fake_pdb = _FakePlatformDB()

    async def _evaluate_subjects(_aid, *, subject="driver", days=30,
                                 write_evidence=False):
        return cards or []

    # Override warehouse safety event reader on the tenant DB itself, so the
    # engine's `tenant.get_safety_events_warehouse(...)` call routes through.
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

    async def _get_loads(_aid, **_kw):
        return loads or []

    async def _get_off_items(_aid, **_kw):
        return off_items or []

    async def _get_settlement_items(_aid, **_kw):
        return pay_items or []

    monkeypatch.setattr(payroll_engine, "get_tenant_db", _get_tenant)
    monkeypatch.setattr(payroll_service, "get_tenant_db", _get_tenant)
    monkeypatch.setattr(payroll_service, "get_db", lambda: fake_pdb)
    monkeypatch.setattr(payroll_engine, "evaluate_subjects", _evaluate_subjects)
    monkeypatch.setattr(payroll_engine.loads_service, "get_loads", _get_loads)
    monkeypatch.setattr(
        payroll_engine.loads_service, "get_off_load_line_items", _get_off_items,
    )
    monkeypatch.setattr(
        payroll_engine.loads_service, "get_settlement_items", _get_settlement_items,
    )
    # Patch bound methods on this specific tenant instance.
    tenant_db.get_safety_events_warehouse = _events  # type: ignore[assignment]
    tenant_db.get_safety_event_counts_grouped = _counts_grouped  # type: ignore[assignment]


# ── pure rule evaluation matrix ──────────────────────────────────

class TestRuleEvaluation:
    def _score_rule(self, threshold=80.0, amount=10000):
        return BonusRule(
            id=1, name="Safe driver",
            kind=KIND_SCORE_THRESHOLD, amount_cents=amount,
            score_min=threshold,
        )

    def _incident_rule(self, max_count=0, event_type="hard_brake", amount=5000):
        return BonusRule(
            id=2, name="No hard brakes",
            kind=KIND_INCIDENT_COUNT, amount_cents=amount,
            event_type=event_type, max_count=max_count,
        )

    def test_score_rule_passes_at_threshold(self):
        entry = evaluate_score_rule(self._score_rule(80.0), 80.0)
        assert entry is not None
        assert entry.amount_cents == 10000

    def test_score_rule_passes_above_threshold(self):
        entry = evaluate_score_rule(self._score_rule(80.0), 95.5)
        assert entry is not None

    def test_score_rule_fails_below_threshold(self):
        assert evaluate_score_rule(self._score_rule(80.0), 79.9) is None

    def test_score_rule_no_score(self):
        assert evaluate_score_rule(self._score_rule(80.0), None) is None

    def test_score_rule_wrong_kind(self):
        assert evaluate_score_rule(self._incident_rule(), 99.0) is None

    def test_incident_rule_passes_at_zero(self):
        entry = evaluate_incident_rule(self._incident_rule(0), 0)
        assert entry is not None

    def test_incident_rule_passes_within_limit(self):
        entry = evaluate_incident_rule(self._incident_rule(2), 2)
        assert entry is not None

    def test_incident_rule_fails_over_limit(self):
        assert evaluate_incident_rule(self._incident_rule(2), 3) is None

    def test_incident_rule_wrong_kind(self):
        assert evaluate_incident_rule(self._score_rule(), 0) is None


# ── DriverPayMixin CRUD round-trip ─────────────────────────────────

class TestDriverPayMixinCRUD:
    async def test_bonus_rule_crud(self, tenant: Database):
        rid = await tenant.create_bonus_rule(
            ACCOUNT_ID, name="r1", kind=KIND_SCORE_THRESHOLD,
            amount_cents=5000, score_min=80.0,
        )
        assert rid > 0

        rules = await tenant.list_bonus_rules(ACCOUNT_ID)
        assert len(rules) == 1
        assert rules[0]["name"] == "r1"

        ok = await tenant.update_bonus_rule(
            ACCOUNT_ID, rid, name="r1-renamed", active=False,
        )
        assert ok
        rule = await tenant.get_bonus_rule(ACCOUNT_ID, rid)
        assert rule and rule["name"] == "r1-renamed"

        active = await tenant.list_bonus_rules(ACCOUNT_ID, active_only=True)
        assert active == []

        await tenant.delete_bonus_rule(ACCOUNT_ID, rid)
        assert await tenant.get_bonus_rule(ACCOUNT_ID, rid) is None

    async def test_driver_pay_settings_upsert(self, tenant: Database):
        await tenant.upsert_driver_pay_settings(
            ACCOUNT_ID, "DRV1", base_pay_cents=200000, opt_in=True,
        )
        s = await tenant.get_driver_pay_settings(ACCOUNT_ID, "DRV1")
        assert s and s["base_pay_cents"] == 200000

        # upsert again should overwrite, not duplicate
        await tenant.upsert_driver_pay_settings(
            ACCOUNT_ID, "DRV1", base_pay_cents=250000, opt_in=False,
        )
        rows = await tenant.list_driver_pay_settings(ACCOUNT_ID)
        assert len(rows) == 1
        assert rows[0]["base_pay_cents"] == 250000
        assert int(rows[0]["opt_in"]) == 0

    async def test_payroll_run_with_items(self, tenant: Database):
        run_id = await tenant.create_driver_pay_run(
            ACCOUNT_ID, period_start="2025-01-01",
            period_end="2025-01-31", created_by=0,
        )
        await tenant.add_driver_pay_run_item(
            run_id=run_id, driver_id="DRV1", driver_name="Alice",
            base_pay_cents=200000, bonus_total_cents=15000,
            total_cents=215000,
            breakdown=[{"rule_id": 1, "name": "Safe", "kind": "score_threshold",
                        "amount_cents": 15000, "detail": "score 90"}],
        )
        await tenant.set_driver_pay_run_total(ACCOUNT_ID, run_id, 215000)

        run = await tenant.get_driver_pay_run(ACCOUNT_ID, run_id)
        assert run and run["status"] == STATUS_DRAFT
        assert run["total_cents"] == 215000

        items = await tenant.get_driver_pay_run_items(run_id)
        assert len(items) == 1
        assert items[0]["breakdown"][0]["amount_cents"] == 15000

        await tenant.set_driver_pay_run_status(ACCOUNT_ID, run_id, STATUS_FINALIZED)
        run = await tenant.get_driver_pay_run(ACCOUNT_ID, run_id)
        assert run["status"] == STATUS_FINALIZED
        assert run.get("finalized_at")


# ── compute_run + service end-to-end with synthetic fleet ────────

class TestComputeRun:
    async def test_three_drivers_two_rules(self, tenant: Database, monkeypatch):
        cards = [
            {"driver_id": "DRV1", "driver_name": "Alice", "score": 95},
            {"driver_id": "DRV2", "driver_name": "Bob",   "score": 70},
            {"driver_id": "DRV3", "driver_name": "Carol", "score": 88},
        ]
        events = [
            {"driver_id": "DRV2", "event_type": "hard_brake"},
            {"driver_id": "DRV2", "event_type": "hard_brake"},
            {"driver_id": "DRV2", "event_type": "hard_brake"},
            {"driver_id": "DRV3", "event_type": "hard_brake"},
        ]
        _patch_services(monkeypatch, tenant, cards=cards, safety_events=events)

        # Rule A: score >= 80 → +$50
        await tenant.create_bonus_rule(
            ACCOUNT_ID, name="Score80", kind=KIND_SCORE_THRESHOLD,
            amount_cents=5000, score_min=80.0,
        )
        # Rule B: hard_brake count <= 1 → +$25
        await tenant.create_bonus_rule(
            ACCOUNT_ID, name="LowHB", kind=KIND_INCIDENT_COUNT,
            amount_cents=2500, event_type="hard_brake", max_count=1,
        )
        for did in ("DRV1", "DRV2", "DRV3"):
            await tenant.upsert_driver_pay_settings(
                ACCOUNT_ID, did, base_pay_cents=100000, opt_in=True,
            )

        items = await compute_run(ACCOUNT_ID, date(2025, 1, 1), date(2025, 1, 31))
        by_driver = {it.driver_id: it for it in items}

        # DRV1 (score 95, 0 events): base + score + low-hb
        assert by_driver["DRV1"].total_cents == 100000 + 5000 + 2500
        # DRV2 (score 70, 3 events): base only
        assert by_driver["DRV2"].total_cents == 100000
        # DRV3 (score 88, 1 event): base + score + low-hb
        assert by_driver["DRV3"].total_cents == 100000 + 5000 + 2500

    async def test_load_earnings_and_extras(self, tenant: Database, monkeypatch):
        """Settlement components: a stored per-load pay wins, the
        percentage model fills loads without one, extra items + off-load
        layover charge onto the same statement, and a driver with no
        samsara link still gets a user-keyed item."""
        acct = await tenant.create_account("Payroll Loads Co")
        u = await tenant.create_user(9301, acct.id, role=Role.DRIVER,
                                     display_name="Eugene B")
        await tenant.link_samsara_driver(acct.id, u.id, "S1")
        loads = [
            {"status": "delivered", "driver_user_id": u.id,
             "driver_pay": 900.0, "total_rate": 3000.0,
             "total_miles": 1000.0, "extra_driver_pay": 150.0},
            {"status": "delivered", "driver_user_id": u.id,
             "driver_pay": None, "total_rate": 2000.0,
             "total_miles": 800.0, "extra_driver_pay": None},
            {"status": "in_transit", "driver_user_id": u.id,   # not delivered
             "driver_pay": 500.0, "total_rate": 1000.0},
            {"status": "delivered", "driver_user_id": 777,     # no user row
             "driver_pay": 400.0, "total_rate": 1200.0},
        ]
        # Extras are now sourced from the itemized driver-pay items
        # (on-load $150 TONU + off-load $300 layover), which is also what
        # the statement lists line by line.
        pay = [
            {"driver_user_id": u.id, "item_date": "2026-07-02", "kind": "tonu",
             "amount": 150.0, "notes": "", "load_id": 1, "load_number": "L1",
             "bucket": "driver_pay"},
            {"driver_user_id": u.id, "item_date": "2026-07-03",
             "kind": "layover", "amount": 300.0, "notes": "sat all day",
             "load_id": None, "load_number": "", "bucket": "driver_pay"},
            # A deduction rides the same list, tagged by bucket → NET.
            {"driver_user_id": u.id, "item_date": "2026-07-01",
             "kind": "advance", "amount": 200.0, "notes": "cash",
             "load_id": None, "load_number": "", "bucket": "deduction"},
        ]
        _patch_services(monkeypatch, tenant, cards=[],
                        loads=loads, pay_items=pay)
        await tenant.upsert_driver_pay_settings(
            acct.id, "S1", base_pay_cents=0, opt_in=True,
            pay_model="percentage", pay_rate=25,
        )
        items = await compute_run(acct.id, date(2026, 7, 1), date(2026, 7, 31))
        by = {it.driver_id: it for it in items}
        s1 = by["S1"]
        # $900 stored + 25% × $2,000 model = $1,400 over 2 delivered loads.
        assert s1.load_earnings_cents == 90000 + 50000
        assert s1.loads_count == 2
        # $150 on-load TONU + $300 layover.
        assert s1.extras_cents == 45000
        assert s1.total_cents == 140000 + 45000
        assert {b.kind for b in s1.breakdown} >= {"load_earnings", "extra_items"}
        # Itemized statement lines are captured for the drawer/print.
        assert len(s1.load_lines) == 2
        assert s1.load_lines[0]["pay_cents"] == 90000        # stored pay wins
        assert {a["kind"] for a in s1.addition_lines} == {"tonu", "layover"}
        assert sum(a["amount_cents"] for a in s1.addition_lines) == s1.extras_cents
        # Deduction reduces gross → net.
        assert s1.deductions_cents == 20000
        assert [d["kind"] for d in s1.deduction_lines] == ["advance"]
        assert s1.net_cents == s1.total_cents - 20000
        # Datatruck-only / unlinked driver: statement still exists.
        assert by["user:777"].load_earnings_cents == 40000

    async def test_opt_out_skipped(self, tenant: Database, monkeypatch):
        cards = [{"driver_id": "DRV1", "driver_name": "Alice", "score": 99}]
        _patch_services(monkeypatch, tenant, cards=cards, safety_events=[])
        await tenant.upsert_driver_pay_settings(
            ACCOUNT_ID, "DRV1", base_pay_cents=100000, opt_in=False,
        )
        items = await compute_run(ACCOUNT_ID, date(2025, 1, 1), date(2025, 1, 31))
        assert items == []


# ── Service-level lifecycle + kill-switch ────────────────────────

class TestServiceLifecycle:
    async def test_kill_switch_blocks_create_rule(
        self, tenant: Database, monkeypatch,
    ):
        _patch_services(monkeypatch, tenant, payroll_enabled=False)
        with pytest.raises(DriverPayDisabledError):
            await payroll_service.create_rule(
                ACCOUNT_ID, user_id=1, name="x",
                kind=KIND_SCORE_THRESHOLD, amount_cents=1000, score_min=80.0,
            )

    async def test_kill_switch_blocks_create_run(
        self, tenant: Database, monkeypatch,
    ):
        _patch_services(monkeypatch, tenant, payroll_enabled=False)
        with pytest.raises(DriverPayDisabledError):
            await payroll_service.create_run(
                ACCOUNT_ID, user_id=1,
                period_start=date(2025, 1, 1), period_end=date(2025, 1, 31),
            )

    async def test_create_rule_validation(self, tenant: Database, monkeypatch):
        _patch_services(monkeypatch, tenant)
        with pytest.raises(ValueError):
            await payroll_service.create_rule(
                ACCOUNT_ID, user_id=1, name="bad", kind="bogus",
                amount_cents=100,
            )
        with pytest.raises(ValueError):
            await payroll_service.create_rule(
                ACCOUNT_ID, user_id=1, name="bad", kind=KIND_SCORE_THRESHOLD,
                amount_cents=0, score_min=80.0,
            )
        with pytest.raises(ValueError):
            await payroll_service.create_rule(
                ACCOUNT_ID, user_id=1, name="missing-min",
                kind=KIND_SCORE_THRESHOLD, amount_cents=100,
            )
        with pytest.raises(ValueError):
            await payroll_service.create_rule(
                ACCOUNT_ID, user_id=1, name="missing-max",
                kind=KIND_INCIDENT_COUNT, amount_cents=100,
                event_type="hard_brake",
            )

    async def test_run_lifecycle_finalize_then_blocks_double(
        self, tenant: Database, monkeypatch,
    ):
        cards = [{"driver_id": "DRV1", "driver_name": "Alice", "score": 99}]
        _patch_services(monkeypatch, tenant, cards=cards, safety_events=[])
        await tenant.upsert_driver_pay_settings(
            ACCOUNT_ID, "DRV1", base_pay_cents=100000, opt_in=True,
        )
        await payroll_service.create_rule(
            ACCOUNT_ID, user_id=1, name="hi", kind=KIND_SCORE_THRESHOLD,
            amount_cents=5000, score_min=80.0,
        )
        run_id = await payroll_service.create_run(
            ACCOUNT_ID, user_id=1,
            period_start=date(2025, 1, 1), period_end=date(2025, 1, 31),
        )
        assert run_id > 0

        detail = await payroll_service.get_run_detail(ACCOUNT_ID, run_id)
        assert detail and detail["status"] == STATUS_DRAFT
        assert detail["total_cents"] == 105000
        assert len(detail["items"]) == 1

        ok = await payroll_service.finalize_run(
            ACCOUNT_ID, run_id, user_id=1,
        )
        assert ok

        # Finalizing again should raise (not draft)
        with pytest.raises(ValueError):
            await payroll_service.finalize_run(
                ACCOUNT_ID, run_id, user_id=1,
            )

    async def test_cancel_run(self, tenant: Database, monkeypatch):
        _patch_services(monkeypatch, tenant, cards=[], safety_events=[])
        # Create a draft directly via tenant
        rid = await tenant.create_driver_pay_run(
            ACCOUNT_ID, period_start="2025-01-01",
            period_end="2025-01-31", created_by=1,
        )
        ok = await payroll_service.cancel_run(ACCOUNT_ID, rid, user_id=1)
        assert ok
        run = await tenant.get_driver_pay_run(ACCOUNT_ID, rid)
        assert run["status"] == "cancelled"


# ── Payroll as a standard module (legacy flag folded) ─────────────

def test_payroll_is_an_accounting_feature_not_a_module():
    """Payroll is an Accounting feature (docs/FEATURES.md), beside Costs /
    Cost Reports / Billing — NOT its own module.  Its flags mask under the
    accounting module; there is no standalone 'payroll' module."""
    from capabilities.permissions.modules import (
        TOGGLEABLE_MODULES, masked_off_flags,
    )
    assert "payroll" not in TOGGLEABLE_MODULES
    # Disabling ACCOUNTING masks the payroll flags (they own only accounting).
    off = masked_off_flags("accounting")
    assert {"can_driver_pay_admin", "can_driver_pay_view_own"} <= off
    # A payroll flag is never masked by any OTHER department alone.
    for mod in ("fleet", "dispatch", "safety", "hr"):
        assert "can_driver_pay_admin" not in masked_off_flags(mod)


def test_account_modules_routes_replace_payroll_enabled():
    """The generic modules endpoints exist (the Permissions-page switches
    finally have a backend); the bespoke payroll-enabled pair is gone."""
    import os
    os.environ.setdefault("JWT_SECRET", "x" * 40)
    from features.settings.account.router import router
    paths = {r.path for r in router.routes if hasattr(r, "path")}
    assert "/admin/account/modules" in paths
    assert not any("payroll-enabled" in p for p in paths)
