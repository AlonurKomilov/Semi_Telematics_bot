"""The billed quantity is the vehicle registry's — not a telematics signal.

Before 2026-09-11 the extras count was ``COUNT(*) FROM vehicle_state_live
WHERE captured_at > now - 3 days``: a Samsara signal.  A truck added by
hand was never billed, a trailer never existed to billing, and an
account whose Samsara integration was paused billed nothing three days
later while it kept using the product.  The owner's rule: Samsara is a
source that feeds 4truck's registry; what the customer has is what is
in their Vehicles list.

These tests pin the four consequences: the count reads the registry
alone; archiving is the one act that stops a charge; the count reaches
the provider on our own clock (the daily job); and the two guards
around the money (drift, jump) behave.
"""

from __future__ import annotations

import pytest


async def _plan_extras(db, tier, price_id, cents=None):
    """Give the seeded plan its own extras Price (every plan carries one
    now; the env-wide one only recognises subscriptions from before)."""
    row = await db.get_plan(tier)
    await db.upsert_plan(tier, label=row["label"], included=row["included"], quotas=row["quotas"],
                         stripe_extra_price_id=price_id, extra_vehicle_cents=cents)

from tests._repo import REPO


async def _truck(db, account_id: int, unit: str, **kw) -> int:
    return await db.add_vehicle(account_id, unit_number=unit, **kw)


# ── what counts ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_every_non_archived_truck_counts_whatever_its_source(pg_db):
    """Manual, Samsara-fed and TMS-projected trucks bill alike — a
    source-dependent count is the bug this replaces."""
    db = pg_db
    acct = await db.create_account("SourcesCo")
    await _truck(db, acct.id, "M1", source="manual")
    await _truck(db, acct.id, "S1", source="samsara", telematics_ref="281474999000001")
    await _truck(db, acct.id, "D1", source="datatruck")
    assert await db.count_billable_vehicles(acct.id) == 3


@pytest.mark.asyncio
async def test_trailers_and_other_rows_are_not_billed(pg_db):
    """Trailers are the owner's reserved decision; until made, they are
    not billed.  ``BILLABLE_VEHICLE_TYPES`` is the one place to change."""
    from adapters.storage.billing import BILLABLE_VEHICLE_TYPES
    assert BILLABLE_VEHICLE_TYPES == ("truck",)
    db = pg_db
    acct = await db.create_account("TrailersCo")
    await _truck(db, acct.id, "T1")
    await _truck(db, acct.id, "TR1", vehicle_type="trailer")
    await _truck(db, acct.id, "X1", vehicle_type="other")
    assert await db.count_billable_vehicles(acct.id) == 1
    # nor do they show as "archived, not billed" — they are simply outside billing
    assert await db.count_unbilled_vehicles(acct.id) == 0


@pytest.mark.asyncio
async def test_no_telemetry_row_is_needed_and_none_is_read(pg_db):
    """A truck that never reported still bills; a live-state row for a
    truck that is not in the registry does not."""
    db = pg_db
    acct = await db.create_account("NoSignalCo")
    await _truck(db, acct.id, "NEVER-REPORTED")
    await db.upsert_vehicle_state(acct.id, [{
        "vehicle_id": "ghost", "vehicle_name": "GHOST", "company_code": "",
        "lat": None, "lon": None, "speed_mph": None, "heading": None,
        "address": "", "engine_state": "", "fuel_pct": None, "def_pct": None,
        "odometer_mi": None, "odometer_time": None, "engine_hours": None,
        "engine_hours_time": None, "fault_count": 0, "dtc_critical_count": 0,
        "last_driver_id": "", "last_driver_name": "",
        "captured_at": "2026-09-11T00:00:00+00:00",
    }])
    assert await db.count_billable_vehicles(acct.id) == 1


@pytest.mark.asyncio
async def test_operator_status_never_changes_the_bill_only_archiving_does(pg_db):
    """yard / shop / available are the operator's words for a truck they
    still have.  Archiving (by a person, or by the departure sweep) is
    the act that ends the charge; restore brings it back."""
    db = pg_db
    acct = await db.create_account("StatusCo")
    yard = await _truck(db, acct.id, "Y1")
    shop = await _truck(db, acct.id, "S1")
    gone = await _truck(db, acct.id, "G1")
    swept = await _truck(db, acct.id, "W1")
    await db.update_vehicle(acct.id, yard, status="yard")
    await db.update_vehicle(acct.id, shop, status="shop")
    assert await db.count_billable_vehicles(acct.id) == 4

    await db.deactivate_vehicle(acct.id, gone)
    await db._db.execute(
        "UPDATE vehicles SET is_active = 0, archived_reason = 'sweep' WHERE id = ?",
        (swept,),
    )
    assert await db.count_billable_vehicles(acct.id) == 2
    assert await db.count_unbilled_vehicles(acct.id) == 2
    names = [v["vehicle_name"] for v in await db.list_unbilled_vehicles(acct.id)]
    assert names == ["G1", "W1"]

    await db.restore_vehicle(acct.id, gone)
    assert await db.count_billable_vehicles(acct.id) == 3


# ── the provider sync's guards ────────────────────────────────────

async def _stripe_account(db, name: str, trucks: int, **sub_fields):
    acct = await db.create_account(name)
    await db.get_or_create_subscription(acct.id, tier="starter")  # 10 included
    await db.update_subscription(
        acct.id, provider="stripe", provider_subscription_id="sub_X",
        provider_extra_item_id="si_extra", **sub_fields,
    )
    for i in range(trucks):
        await _truck(db, acct.id, f"U{i}")
    return acct


def _fake_stripe(monkeypatch, current_qty: int, patched: dict):
    class _FakeStripe:
        class SubscriptionItem:
            @staticmethod
            def retrieve(_id):
                return {"quantity": current_qty}

            @staticmethod
            def modify(item_id, *, quantity, proration_behavior):
                patched["qty"] = quantity
    monkeypatch.setattr(
        "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe,
    )


@pytest.mark.asyncio
async def test_sync_records_the_quantity_it_confirmed(pg_db, monkeypatch):
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    acct = await _stripe_account(db, "RecordCo", trucks=13)  # 3 extras
    patched: dict = {}
    _fake_stripe(monkeypatch, current_qty=1, patched=patched)
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] is None and patched == {"qty": 3}
    sub = await db.get_subscription(acct.id)
    assert sub["billed_quantity"] == 3 and sub["billed_at"]
    assert "drift" not in r  # first sync: no baseline to drift from


@pytest.mark.asyncio
async def test_sync_skips_a_subscription_that_is_not_live(pg_db, monkeypatch):
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    acct = await _stripe_account(db, "CanceledCo", trucks=13, status="canceled")
    patched: dict = {}
    _fake_stripe(monkeypatch, current_qty=0, patched=patched)
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] == "not_live" and patched == {}


@pytest.mark.asyncio
async def test_a_first_sync_may_jump_but_a_later_one_is_held(pg_db, monkeypatch):
    """The guard needs a baseline: a fleet's first sync after checkout is
    whatever the fleet is.  Once a quantity has been confirmed, a rise
    beyond max(20, half the current) waits for an operator."""
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    acct = await _stripe_account(db, "JumpCo", trucks=60)  # 50 extras
    patched: dict = {}
    _fake_stripe(monkeypatch, current_qty=0, patched=patched)
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] is None and patched == {"qty": 50}

    # Now a baseline exists (50).  Someone projects 60 more trucks.
    for i in range(60):
        await _truck(db, acct.id, f"P{i}")
    patched.clear()
    _fake_stripe(monkeypatch, current_qty=50, patched=patched)
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] == "jump_guard"
    assert (r["before"], r["after"]) == (50, 110)
    assert patched == {}
    # the baseline is untouched by a held sync
    assert (await db.get_subscription(acct.id))["billed_quantity"] == 50

    # The operator, having looked, releases it from the console.
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db, force=True)
    assert r["skipped"] is None and patched == {"qty": 110}
    assert (await db.get_subscription(acct.id))["billed_quantity"] == 110


@pytest.mark.asyncio
async def test_a_small_rise_and_any_decrease_go_through_unattended(pg_db, monkeypatch):
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    acct = await _stripe_account(db, "StepCo", trucks=40, billed_quantity=30)
    patched: dict = {}
    _fake_stripe(monkeypatch, current_qty=30, patched=patched)
    # 40 trucks = 30 extras = current → noop, and the confirmation is re-stamped
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] == "noop" and patched == {}

    for i in range(15):                      # +15 ≤ max(20, 15): allowed
        await _truck(db, acct.id, f"A{i}")
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] is None and patched == {"qty": 45}

    # Archive 30 of them: a decrease is never held — holding it would overcharge.
    ids = [v.id for v in await db.list_vehicles(acct.id)][:30]
    for vid in ids:
        await db.deactivate_vehicle(acct.id, vid)
    patched.clear()
    _fake_stripe(monkeypatch, current_qty=45, patched=patched)
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] is None and patched == {"qty": 15}


@pytest.mark.asyncio
async def test_a_quantity_changed_outside_4truck_is_flagged_and_reconciled(pg_db, monkeypatch):
    """We last set 3; Stripe now says 7 — someone edited it in the Stripe
    dashboard.  The registry is the source of truth: it goes back to
    what the fleet is, and the result says drift so the caller can log
    or alert."""
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    acct = await _stripe_account(db, "DriftCo", trucks=13, billed_quantity=3)
    patched: dict = {}
    _fake_stripe(monkeypatch, current_qty=7, patched=patched)
    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["drift"] is True
    assert r["skipped"] is None and patched == {"qty": 3}


@pytest.mark.asyncio
async def test_a_failed_local_record_does_not_unsay_a_successful_patch(pg_db, monkeypatch):
    """Stripe was PATCHed; then writing billed_quantity failed.  The money
    side succeeded and the result must say so — a caller reading it as
    a failed sync would retry or alarm for nothing.  The result carries
    ``recorded: False`` so the gap is visible."""
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    acct = await _stripe_account(db, "BookkeepingCo", trucks=13)
    patched: dict = {}
    _fake_stripe(monkeypatch, current_qty=1, patched=patched)
    real_update = db.update_subscription

    async def _flaky(account_id, **fields):
        if "billed_quantity" in fields:
            raise RuntimeError("column missing on a not-yet-migrated node")
        return await real_update(account_id, **fields)
    monkeypatch.setattr(db, "update_subscription", _flaky)

    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] is None and patched == {"qty": 3}
    assert r["recorded"] is False
    assert (await db.get_subscription(acct.id))["billed_quantity"] is None


# ── the daily job: our clock, not the provider's ──────────────────

class _Acct:
    def __init__(self, id: int):
        self.id = id


class _Platform:
    def __init__(self, ids):
        self._ids = ids

    async def list_accounts(self, active_only=True):
        return [_Acct(i) for i in self._ids]


class _Provider:
    def __init__(self, outcomes: dict):
        self._outcomes = outcomes
        self.calls: list[tuple[int, bool]] = []

    async def sync_billing_quantity(self, account_id, db, *, force=False):
        self.calls.append((account_id, force))
        out = self._outcomes[account_id]
        if isinstance(out, Exception):
            raise out
        return {"skipped": out, "account_id": account_id}


@pytest.mark.asyncio
async def test_daily_job_visits_every_account_and_one_failure_blocks_nobody(monkeypatch):
    import infra.platform as _ip
    import capabilities.platform.billing as billing_pkg
    from capabilities.platform.billing.jobs import run_billing_quantity_sync

    provider = _Provider({
        1: None,                      # patched
        2: "noop",
        3: "not_stripe",
        4: RuntimeError("db gone"),   # raised
        5: "stripe_error",            # the provider's own failure token
        6: None,                      # patched, after the failures
    })

    class _Router:
        platform = _Platform([1, 2, 3, 4, 5, 6])
    monkeypatch.setattr(_ip, "get_router", lambda: _Router())
    monkeypatch.setattr(billing_pkg, "get_provider", lambda: provider)

    result = await run_billing_quantity_sync()
    assert result == {
        "patched": 2, "noop": 1, "skipped": 1, "failed": 2, "total": 6,
    }
    assert [c[0] for c in provider.calls] == [1, 2, 3, 4, 5, 6]
    assert all(force is False for _, force in provider.calls), (
        "the unattended job must never lift the jump guard")


def test_the_job_is_on_the_scheduler_and_in_the_console_catalog():
    src = (REPO / "interfaces" / "bot" / "scheduler.py").read_text()
    assert 'id="billing_quantity_sync"' in src
    assert '"billing_quantity_sync":' in src, "operator console catalog entry"
    assert "run_billing_quantity_sync" in src


# ── the extras line that did not exist yet ────────────────────────

@pytest.mark.asyncio
async def test_the_sync_opens_an_extras_line_the_checkout_never_created(pg_db, monkeypatch):
    """An account that checked out with nothing above its plan has no
    extras line at all — Stripe refuses one of quantity 0. The first
    truck it gains past the included count has to CREATE that line, or
    the extra never reaches an invoice."""
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extras_test")
    acct = await db.create_account("GrewLaterCo")
    await _plan_extras(db, "starter", "price_extras_test")
    await db.get_or_create_subscription(acct.id, tier="starter")     # 10 included
    await db.update_subscription(
        acct.id, provider="stripe", provider_subscription_id="sub_X",
        provider_extra_item_id="",                                   # no extras line at all
    )
    for i in range(12):                                              # → 2 extras now
        await _truck(db, acct.id, f"G{i}")

    created: dict = {}

    class _FakeStripe:
        class SubscriptionItem:
            @staticmethod
            def create(**kw):
                if int(kw.get("quantity", 0)) < 1:
                    raise ValueError("This value must be greater than or equal to 1.")
                created.update(kw)
                return {"id": "si_new"}

            @staticmethod
            def retrieve(_id):
                raise AssertionError("there is no item to retrieve yet")
    monkeypatch.setattr(
        "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe)

    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] is None and (r["before"], r["after"]) == (0, 2)
    assert created["quantity"] == 2 and created["subscription"] == "sub_X"
    sub = await db.get_subscription(acct.id)
    assert sub["provider_extra_item_id"] == "si_new"
    assert sub["billed_quantity"] == 2


@pytest.mark.asyncio
async def test_no_extras_line_is_opened_for_an_account_that_owes_none(pg_db, monkeypatch):
    """Under the included count there is nothing to create — and Stripe
    would refuse the quantity 0 line anyway."""
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    db = pg_db
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extras_test")
    acct = await db.create_account("StillSmallCo")
    await _plan_extras(db, "starter", "price_extras_test")
    await db.get_or_create_subscription(acct.id, tier="starter")
    await db.update_subscription(
        acct.id, provider="stripe", provider_subscription_id="sub_X",
        provider_extra_item_id="")
    for i in range(4):
        await _truck(db, acct.id, f"T{i}")

    class _FakeStripe:
        class SubscriptionItem:
            @staticmethod
            def create(**kw):
                raise AssertionError("nothing should be created")
    monkeypatch.setattr(
        "capabilities.platform.billing.stripe_client._stripe", lambda: _FakeStripe)

    r = await StripeBillingProvider().sync_billing_quantity(acct.id, db)
    assert r["skipped"] == "noop" and r["after"] == 0


@pytest.mark.asyncio
async def test_a_stripe_refusal_at_checkout_is_not_an_internal_error(pg_db, monkeypatch):
    """The customer is the one reading the answer. A 500 says 4truck is
    broken; a ProviderError becomes a 502 whose text names the provider
    and what it refused — which is what the operator needs too."""
    from capabilities.platform.billing.provider import ProviderError
    from capabilities.platform.billing.stripe_client import StripeBillingProvider
    monkeypatch.setenv("STRIPE_PRICE_STARTER", "price_starter_test")
    monkeypatch.delenv("STRIPE_PRICE_EXTRA_VEHICLE", raising=False)
    db = pg_db
    acct = await db.create_account("RefusedCo")
    await _plan_extras(db, "starter", "price_extras_test")   # the refusal under test is Stripe's, not ours
    await db.get_or_create_subscription(acct.id, tier="free")

    class _Refusing:
        class error:
            StripeError = RuntimeError            # what the SDK raises

        class Customer:
            @staticmethod
            def create(**kw): return {"id": "cus_x"}

        class checkout:
            class Session:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("This value must be greater than or equal to 1.")
    monkeypatch.setattr(
        "capabilities.platform.billing.stripe_client._stripe", lambda: _Refusing)

    with pytest.raises(ProviderError) as got:
        await StripeBillingProvider().create_checkout_session(
            acct.id, db, "starter", "ok", "cancel")
    assert "Stripe could not start the checkout" in str(got.value)
