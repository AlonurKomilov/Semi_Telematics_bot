"""The price rollout engine against a fake Stripe and an in-memory store.

What it pins: the Price is created on the plan's Product with the
idempotency keys the design names; a mismatched Stripe price refuses
the rollout; each subscription gets exactly one of the named outcomes;
only the base item is modified, with proration "none"; the batch cap
hands back ``remaining`` and a second call resumes without touching
finished accounts; five errors in a row abort.
"""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.platform.billing import rollout as R


# ── fakes ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _extras_price(monkeypatch):
    """The extras item is told apart from the base by its price id (the
    same rule the webhook uses); without it a sub with only an extras
    item would look like it has a base."""
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")

def _item(item_id, price_id, amount, interval="month", count=1, currency="usd"):
    return {"id": item_id, "price": {"id": price_id, "unit_amount": amount, "currency": currency,
                                     "recurring": {"interval": interval, "interval_count": count}}}


class FakeStripe:
    def __init__(self, subs: dict, price: dict | None = None, prices: dict | None = None):
        self.subs = subs                       # sub_id → subscription object
        self.price = price or {"id": "price_new", "active": True, "unit_amount": 5900, "currency": "usd",
                               "recurring": {"interval": "month", "interval_count": 1}}
        # other Prices Stripe knows, by id (the plan's extras Price); the
        # default answers for anything else, as before
        self.prices = prices or {}
        self.calls: list = []
        fake = self

        class Price:
            @staticmethod
            def retrieve(pid):
                fake.calls.append(("Price.retrieve", pid))
                return fake.prices.get(pid, fake.price)
            @staticmethod
            def create(**kw): fake.calls.append(("Price.create", kw)); return {"id": "price_created"}
            @staticmethod
            def modify(pid, **kw): fake.calls.append(("Price.modify", pid, kw)); return {"id": pid, **kw}

        class Product:
            @staticmethod
            def create(**kw): fake.calls.append(("Product.create", kw)); return {"id": "prod_created"}

        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None):
                fake.calls.append(("Subscription.retrieve", sid))
                s = fake.subs[sid]
                if isinstance(s, Exception):
                    raise s
                return s
            @staticmethod
            def modify(sid, **kw):
                fake.calls.append(("Subscription.modify", sid, kw))
                s = dict(fake.subs[sid])
                # every item named in the call moves to its price, at that
                # price's amount; the rest stay as they were
                moved = {it["id"]: it for it in kw["items"]}
                new_items = []
                for it in s["items"]["data"]:
                    if it["id"] in moved:
                        target = moved[it["id"]]["price"]
                        amount = fake.prices.get(target, fake.price)["unit_amount"]
                        meta = fake.prices.get(target, {}).get("metadata")
                        new = _item(it["id"], target, amount)
                        if meta:
                            new["price"]["metadata"] = meta
                        new_items.append(new)
                    else:
                        new_items.append(it)
                s["items"] = {"data": new_items}
                fake.subs[sid] = s
                return s

        self.Price, self.Product, self.Subscription = Price, Product, Subscription


class FakeDB:
    """Just enough of PlansMixin + BillingMixin + PlanRolloutsMixin."""

    def __init__(self, plan: dict, subs: list[dict]):
        self.plan = plan
        self.subs = {s["account_id"]: dict(s) for s in subs}
        self.rollouts: dict[int, dict] = {}
        self.items: dict[tuple, dict] = {}
        self.updates: list = []

    async def get_plan(self, tier): return dict(self.plan) if self.plan["tier"] == tier else None
    async def subscriptions_on_tier(self, tier):
        return [dict(s) for s in self.subs.values()
                if s["tier"] == tier and s["provider"] == "stripe" and s["provider_subscription_id"]
                and s["status"] in ("active", "trialing", "past_due")]
    async def open_price_rollout(self, tier):
        for rid in sorted(self.rollouts, reverse=True):
            r = self.rollouts[rid]
            if r["tier"] == tier and r["finished_at"] is None:
                return dict(r)
        return None
    async def abort_stale_rollouts(self, tier, older_than_minutes=15): return 0
    async def claim_rollout(self, rollout_id, token, stale_minutes=5):
        r = self.rollouts[rollout_id]
        if r.get("running_token") and r["running_token"] != token:
            return False
        r["running_token"] = token; return True
    async def release_rollout(self, rollout_id, token):
        r = self.rollouts[rollout_id]
        if r.get("running_token") == token:
            r["running_token"] = ""; r["last_batch_at"] = "now"
    async def create_price_rollout(self, tier, **kw):
        rid = len(self.rollouts) + 1
        self.rollouts[rid] = {"id": rid, "tier": tier, "finished_at": None, "aborted": 0, **kw}
        return rid
    async def record_rollout_item(self, rollout_id, account_id, **kw):
        self.items[(rollout_id, account_id)] = {"rollout_id": rollout_id, "account_id": account_id, **kw}
    async def rollout_items(self, rollout_id):
        return [dict(v) for (rid, _), v in self.items.items() if rid == rollout_id]
    async def finish_price_rollout(self, rollout_id, aborted=False, summary=None):
        self.rollouts[rollout_id].update(finished_at="now", aborted=1 if aborted else 0, summary=summary or {})
    async def list_price_rollouts(self, tier, limit=10): return [r for r in self.rollouts.values() if r["tier"] == tier]
    async def update_subscription(self, account_id, **fields):
        self.updates.append((account_id, fields)); self.subs[account_id].update(fields)


def _sub_row(account_id, sub_id, price_id="price_old", status="active"):
    return {"account_id": account_id, "tier": "starter", "provider": "stripe", "provider_subscription_id": sub_id,
            "status": status, "monthly_base_usd": 4900, "provider_base_price_id": price_id, "provider_base_item_id": ""}


def _stripe_sub(base_price="price_old", base_amount=4900, status="active", schedule=None, extras=True):
    items = [_item("si_base", base_price, base_amount)]
    if extras:
        items.append(_item("si_extra", "price_extra", 299))
    return {"id": "sub", "status": status, "schedule": schedule, "current_period_end": 1800000000,
            "items": {"data": items}}


PLAN = {"tier": "starter", "label": "Starter", "price_monthly_cents": 5900, "stripe_price_id": "price_new",
        "stripe_product_id": "prod_1", "updated_at": "2026-09-11T10:00:00+00:00"}


# ── the Price behind the row ──────────────────────────────────────

def test_the_price_is_created_on_the_plans_product_with_the_keys_the_design_names():
    st = FakeStripe({})
    out = R.ensure_plan_price(st, tier="starter", label="Starter", cents=5900,
                              before={"stripe_product_id": "", "stripe_price_id": "price_old", "updated_at": "T1"})
    assert out == {"stripe_price_id": "price_created", "stripe_product_id": "prod_created", "archived": "price_old"}
    names = [c[0] for c in st.calls]
    assert names == ["Product.create", "Price.create"]
    prod_kw, price_kw = st.calls[0][1], st.calls[1][1]
    assert prod_kw["idempotency_key"] == "plan-product:starter" and prod_kw["metadata"] == {"tier": "starter"}
    assert price_kw["product"] == "prod_created" and price_kw["unit_amount"] == 5900 and price_kw["currency"] == "usd"
    assert price_kw["recurring"] == {"interval": "month"} and price_kw["lookup_key"] == "4truck_starter_monthly"
    assert price_kw["idempotency_key"] == "plan-price:starter:5900:T1"         # the row's stamp keeps a 49→59→49 flip honest
    # a plan that already has a Product reuses it; zero cents means no Price at all
    st2 = FakeStripe({})
    out2 = R.ensure_plan_price(st2, tier="starter", label="Starter", cents=6900, before=PLAN)
    assert out2["stripe_product_id"] == "prod_1" and [c[0] for c in st2.calls] == ["Price.create"]
    assert R.ensure_plan_price(FakeStripe({}), tier="free", label="Free", cents=0, before=PLAN) == \
        {"stripe_price_id": "", "stripe_product_id": "prod_1", "archived": "price_new"}
    assert R.archive_price(st, "price_old") is True and st.calls[-1] == ("Price.modify", "price_old", {"active": False})
    assert R.archive_price(st, "") is False


# ── refusals ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_stripe_price_that_does_not_match_the_row_refuses_the_rollout():
    db = FakeDB(PLAN, [_sub_row(1, "sub_1")])
    st = FakeStripe({"sub_1": _stripe_sub()}, price={"id": "price_new", "active": True, "unit_amount": 4900,
                                                     "currency": "usd", "recurring": {"interval": "month", "interval_count": 1}})
    with pytest.raises(R.RolloutRefused, match="save the plan again"):
        await R.execute(st, db, "starter", actor="tg:1")
    st.price["unit_amount"] = 5900; st.price["recurring"] = {"interval": "year", "interval_count": 1}
    with pytest.raises(R.RolloutRefused, match="monthly USD"):
        await R.execute(st, db, "starter", actor="tg:1")
    st.price["recurring"] = {"interval": "month", "interval_count": 1}; st.price["active"] = False
    with pytest.raises(R.RolloutRefused, match="archived"):
        await R.execute(st, db, "starter", actor="tg:1")
    assert not any(c[0] == "Subscription.modify" for c in st.calls)
    db2 = FakeDB({**PLAN, "stripe_price_id": ""}, [])
    with pytest.raises(R.RolloutRefused, match="no Stripe price"):
        await R.execute(FakeStripe({}), db2, "starter", actor="tg:1")


# ── outcomes ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_each_subscription_gets_one_outcome_and_only_the_base_item_moves():
    db = FakeDB(PLAN, [_sub_row(1, "sub_1"), _sub_row(2, "sub_2", price_id="price_new"),
                       _sub_row(3, "sub_3"), _sub_row(4, "sub_4"), _sub_row(5, "sub_5"),
                       {**_sub_row(6, ""), "provider_subscription_id": ""},      # not a candidate
                       {**_sub_row(7, "sub_7"), "provider": "stub"}])            # not a candidate
    st = FakeStripe({
        "sub_1": _stripe_sub(),                                    # → changed
        "sub_2": _stripe_sub(base_price="price_new", base_amount=5900),   # → already
        "sub_3": _stripe_sub(status="canceled"),                   # → skipped_status (and reconciled)
        "sub_4": _stripe_sub(schedule="sub_sched_1"),              # → has_schedule
        "sub_5": {"id": "sub_5", "status": "active", "items": {"data": [_item("si_x", "price_extra", 299)]}},  # → no_base_item
    })
    out = await R.execute(st, db, "starter", actor="tg:1")
    assert out["counts"] == {"changed": 1, "already": 1, "skipped_status": 1, "has_schedule": 1, "no_base_item": 1}
    assert out["remaining"] == 0 and out["finished"] is True and out["aborted"] is False
    mods = [c for c in st.calls if c[0] == "Subscription.modify"]
    assert len(mods) == 1 and mods[0][1] == "sub_1"
    kw = mods[0][2]
    assert kw["items"] == [{"id": "si_base", "price": "price_new", "quantity": 1}]     # never the extras item
    assert kw["proration_behavior"] == "none"                                            # from the next period
    assert kw["idempotency_key"] == "rollout:1:price_new"
    # our rows: the moved and the already-there record the new price; the dead one is reconciled
    assert db.subs[1]["provider_base_price_id"] == "price_new" and db.subs[1]["monthly_base_usd"] == 5900
    assert db.subs[2]["provider_base_price_id"] == "price_new" and db.subs[2]["monthly_base_usd"] == 5900
    assert db.subs[3]["status"] == "canceled"
    assert {i["account_id"]: i["outcome"] for i in db.items.values()} == {1: "changed", 2: "already", 3: "skipped_status", 4: "has_schedule", 5: "no_base_item"}
    assert db.rollouts[1]["finished_at"] and db.rollouts[1]["summary"]["changed"] == 1
    assert (await R.preview(db, "starter"))["open_rollout"] is None


@pytest.mark.asyncio
async def test_the_batch_cap_hands_back_remaining_and_a_second_call_resumes_without_repeating():
    subs = [_sub_row(i, f"sub_{i}") for i in range(1, 8)]
    db = FakeDB(PLAN, subs)
    st = FakeStripe({f"sub_{i}": _stripe_sub() for i in range(1, 8)})
    first = await R.execute(st, db, "starter", actor="tg:1", limit=3)
    assert first["processed"] == 3 and first["remaining"] == 4 and first["finished"] is False
    assert db.rollouts[first["rollout_id"]]["finished_at"] is None
    second = await R.execute(st, db, "starter", actor="tg:1", limit=10)
    assert second["rollout_id"] == first["rollout_id"]                      # resumed, not restarted
    assert second["processed"] == 4 and second["remaining"] == 0 and second["finished"] is True
    moved = [c[1] for c in st.calls if c[0] == "Subscription.modify"]
    assert sorted(moved) == [f"sub_{i}" for i in range(1, 8)] and len(moved) == 7   # each exactly once


@pytest.mark.asyncio
async def test_five_errors_in_a_row_abort_the_rollout_and_one_error_does_not():
    subs = [_sub_row(i, f"sub_{i}") for i in range(1, 8)]
    db = FakeDB(PLAN, subs)
    st = FakeStripe({f"sub_{i}": RuntimeError("stripe down") for i in range(1, 8)})
    out = await R.execute(st, db, "starter", actor="tg:1")
    assert out["aborted"] is True and out["counts"] == {"error": 5} and out["remaining"] == 2
    assert db.rollouts[1]["aborted"] == 1 and db.rollouts[1]["finished_at"]
    assert all(i["outcome"] == "error" and "stripe down" in i["error"] for i in db.items.values())
    # one failure among successes is recorded and the run goes on
    db2 = FakeDB(PLAN, [_sub_row(1, "sub_1"), _sub_row(2, "sub_2"), _sub_row(3, "sub_3")])
    st2 = FakeStripe({"sub_1": _stripe_sub(), "sub_2": RuntimeError("blip"), "sub_3": _stripe_sub()})
    out2 = await R.execute(st2, db2, "starter", actor="tg:1")
    assert out2["counts"] == {"changed": 2, "error": 1} and out2["aborted"] is False and out2["finished"] is True


@pytest.mark.asyncio
async def test_a_batch_already_running_is_told_so_and_the_claim_is_released_after_a_batch():
    db = FakeDB(PLAN, [_sub_row(1, "sub_1"), _sub_row(2, "sub_2")])
    st = FakeStripe({"sub_1": _stripe_sub(), "sub_2": _stripe_sub()})
    first = await R.execute(st, db, "starter", actor="tg:1", limit=1)
    assert first["remaining"] == 1 and db.rollouts[first["rollout_id"]]["running_token"] == ""   # released
    db.rollouts[first["rollout_id"]]["running_token"] = "someone-else"                              # a batch in flight
    with pytest.raises(R.RolloutBusy):
        await R.execute(st, db, "starter", actor="tg:2")
    db.rollouts[first["rollout_id"]]["running_token"] = ""
    second = await R.execute(st, db, "starter", actor="tg:2")
    assert second["finished"] is True and db.rollouts[first["rollout_id"]]["last_batch_at"] == "now"


def test_moving_a_plan_to_free_hands_back_its_old_price_to_be_archived():
    assert R.ensure_plan_price(FakeStripe({}), tier="free", label="Free", cents=0,
                               before={"stripe_product_id": "prod_1", "stripe_price_id": "price_old"}) == \
        {"stripe_price_id": "", "stripe_product_id": "prod_1", "archived": "price_old"}


@pytest.mark.asyncio
async def test_preview_counts_from_our_tables_and_names_the_open_rollout():
    db = FakeDB(PLAN, [_sub_row(1, "sub_1"), _sub_row(2, "sub_2", price_id="price_new"), _sub_row(3, "sub_3", status="canceled")])
    p = await R.preview(db, "starter")
    assert (p["candidates"], p["already_on_new_price"], p["to_move"], p["to_cents"], p["to_price_id"]) == (2, 1, 1, 5900, "price_new")
    assert p["open_rollout"] is None
    with pytest.raises(R.RolloutRefused):
        await R.preview(db, "nope")


# ── the extras Price rides the rollout ────────────────────

X_PRICE = {"id": "price_x_new", "active": True, "unit_amount": 349, "currency": "usd",
           "recurring": {"interval": "month", "interval_count": 1}, "metadata": {"tier": "starter", "kind": "extra"}}
PLAN_X = {**PLAN, "extra_vehicle_cents": 349, "stripe_extra_price_id": "price_x_new"}


def test_the_extras_price_is_made_on_the_same_product_and_says_what_it_is():
    st = FakeStripe({})
    out = R.ensure_extra_price(st, tier="starter", label="Starter", cents=349, before=PLAN)
    assert out == {"stripe_extra_price_id": "price_created", "stripe_product_id": "prod_1", "archived": ""}
    assert [c[0] for c in st.calls] == ["Price.create"], "the Product the base Price hangs off is reused"
    kw = st.calls[0][1]
    assert kw["product"] == "prod_1" and kw["unit_amount"] == 349 and kw["recurring"] == {"interval": "month"}
    assert kw["metadata"] == {"tier": "starter", "kind": "extra"}, "the item is known for what it is, even archived"
    assert kw["lookup_key"] == "4truck_starter_extra"
    assert kw["idempotency_key"] == "plan-extra-price:starter:349:2026-09-11T10:00:00+00:00"
    # a second save at another amount hands the old one back to be archived
    out2 = R.ensure_extra_price(FakeStripe({}), tier="starter", label="Starter", cents=399, before=PLAN_X)
    assert out2["archived"] == "price_x_new"
    # no extra truck billed: no Price, and the one it had goes
    assert R.ensure_extra_price(FakeStripe({}), tier="starter", label="Starter", cents=0, before=PLAN_X) == \
        {"stripe_extra_price_id": "", "stripe_product_id": "prod_1", "archived": "price_x_new"}


@pytest.mark.asyncio
async def test_a_subscriber_on_the_env_wide_extras_price_moves_both_items_in_one_call():
    """The rule that keeps existing subscribers from carrying the
    mismatch forever: a rollout moves the extras item too, onto the
    plan's own Price, in the SAME modify as the base, with no proration."""
    db = FakeDB(PLAN_X, [_sub_row(1, "sub_1"), _sub_row(2, "sub_2", price_id="price_new")])
    st = FakeStripe({
        "sub_1": _stripe_sub(),                                          # base old, extras on the env Price → both move
        "sub_2": {"id": "sub_2", "status": "active", "current_period_end": 1800000000,
                  "items": {"data": [_item("si_base", "price_new", 5900),
                                     {**_item("si_extra", "price_x_new", 349), "quantity": 12}]}},
    }, prices={"price_x_new": X_PRICE})
    st.subs["sub_2"]["items"]["data"][1]["price"]["metadata"] = X_PRICE["metadata"]
    out = await R.execute(st, db, "starter", actor="tg:1")
    assert out["counts"] == {"changed": 1, "already": 1}, out
    mods = [c for c in st.calls if c[0] == "Subscription.modify"]
    assert len(mods) == 1 and mods[0][1] == "sub_1"
    kw = mods[0][2]
    assert kw["items"] == [{"id": "si_base", "price": "price_new", "quantity": 1},
                           {"id": "si_extra", "price": "price_x_new", "quantity": 1}]
    assert kw["proration_behavior"] == "none"
    assert kw["idempotency_key"] == "rollout:1:price_new:price_x_new"
    assert db.subs[1]["provider_base_price_id"] == "price_new" and db.subs[1]["extra_vehicle_cents"] == 349
    # and the extras Price is checked against the row like the base one
    db2 = FakeDB({**PLAN_X, "stripe_extra_price_id": ""}, [_sub_row(1, "sub_1")])
    with pytest.raises(R.RolloutRefused, match="extra trucks but has no Stripe price"):
        await R.execute(FakeStripe({"sub_1": _stripe_sub()}), db2, "starter", actor="tg:1")
    wrong = FakeStripe({"sub_1": _stripe_sub()}, prices={"price_x_new": {**X_PRICE, "unit_amount": 299}})
    with pytest.raises(R.RolloutRefused, match="save the plan again"):
        await R.execute(wrong, FakeDB(PLAN_X, [_sub_row(1, "sub_1")]), "starter", actor="tg:1")
