"""``/system/plans`` — the operator reads and writes what each plan includes.

The write is validated against the sellable set, audited, and reaches
the resolver at once: after narrowing Free, an account on Free loses the
feature and an account on Pro keeps it.
"""

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from adapters.storage import Role
from interfaces.api.auth import create_jwt


@pytest_asyncio.fixture
async def system_app(pg_db, monkeypatch):
    db = pg_db
    acct_free = await db.create_account("Plans Test Free Co")
    acct_pro = await db.create_account("Plans Test Pro Co", tier="pro")
    op = await db.create_user(910001, acct_free.id, role=Role.OWNER)
    non_op = await db.create_user(910002, acct_free.id, role=Role.OWNER)
    await db.create_user(910003, acct_pro.id, role=Role.OWNER)

    import capabilities.permissions.roles as perms
    monkeypatch.setattr(perms, "SYSTEM_OWNER_IDS", {op.telegram_id})

    import infra.platform as _cp
    monkeypatch.setattr(_cp, "_db", db)

    from interfaces.api.app import create_api
    app = create_api()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {
            "client": client, "db": db, "free": acct_free, "pro": acct_pro,
            "op": {"Authorization": f"Bearer {create_jwt(op.telegram_id, acct_free.id, 'owner')}"},
            "non_op": {"Authorization": f"Bearer {create_jwt(non_op.telegram_id, acct_free.id, 'owner')}"},
        }


@pytest.mark.asyncio
async def test_get_lists_every_plan_the_catalog_and_the_blast_radius(system_app):
    s = system_app
    r = await s["client"].get("/api/system/plans", headers=s["op"])
    assert r.status_code == 200
    body = r.json()
    plans = {p["tier"]: p for p in body["plans"]}
    assert {"free", "starter", "pro", "enterprise"} <= set(plans)
    assert all(p["everything"] and p["included"] == ["*"] for p in plans.values())
    assert plans["free"]["accounts"] >= 1 and plans["pro"]["accounts"] >= 1
    assert set(plans["free"]["quota_defaults"]) == {"max_users", "max_companies"}
    ids = {c["id"] for c in body["catalog"]}
    assert "ai_assistant" in ids and "maintenance" in ids
    assert not ({"billing", "overview", "team_management", "scheduled_reports"} & ids)
    assert body["quota_keys"] == ["max_users", "max_companies"]
    assert body["accounts_without_plan"] == {}


@pytest.mark.asyncio
async def test_only_a_system_owner_may_read_or_write(system_app):
    s = system_app
    assert (await s["client"].get("/api/system/plans", headers=s["non_op"])).status_code == 403
    r = await s["client"].put("/api/system/plans/free", headers=s["non_op"],
                              json={"label": "Free", "included": ["*"], "quotas": {}})
    assert r.status_code == 403
    assert (await s["client"].get("/api/system/plans")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_narrowing_a_plan_reaches_the_resolver_and_the_audit(system_app):
    from capabilities.permissions.plans import EXCLUDABLE
    from capabilities.permissions.roles import get_account_permissions
    s = system_app
    # before: everyone holds maintenance
    assert (await get_account_permissions(Role.OWNER, s["free"].id)).can_view_maintenance
    assert (await get_account_permissions(Role.OWNER, s["pro"].id)).can_view_maintenance

    keep = [i for i in EXCLUDABLE if i != "maintenance"]
    r = await s["client"].put("/api/system/plans/free", headers=s["op"],
                              json={"label": "Free", "included": keep, "quotas": {"max_users": 5}})
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["everything"] is False and "maintenance" not in plan["included"]
    assert plan["quotas"] == {"max_users": 5} and plan["updated_by"].startswith("tg:")

    # after: Free lost it, Pro kept it — through the permission, at once
    free = await get_account_permissions(Role.OWNER, s["free"].id)
    assert not free.can_view_maintenance and not free.can_manage_maintenance
    assert free.can_view_vehicles and free.can_manage_billing              # the way back stays open
    assert (await get_account_permissions(Role.OWNER, s["pro"].id)).can_view_maintenance

    # a job with no user in hand hears the same answer
    from capabilities.permissions.modules import feature_available
    assert not feature_available(await s["db"].get_account(s["free"].id), "maintenance")
    assert feature_available(await s["db"].get_account(s["pro"].id), "maintenance")

    # the quota now reads the plan row
    from capabilities.permissions.plans import quota_for
    assert quota_for("free", "max_users", 999) == 5

    rows = await s["db"].list_platform_audit(event="plan.updated", limit=5)
    assert rows and rows[0]["actor"].startswith("tg:") and '"tier": "free"' in rows[0]["details"]

    # GET reflects it
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    assert "maintenance" not in {p["tier"]: p for p in g["plans"]}["free"]["included"]


@pytest.mark.asyncio
async def test_a_write_is_refused_not_silently_dropped(system_app):
    s = system_app
    put = lambda tier, body: s["client"].put(f"/api/system/plans/{tier}", headers=s["op"], json=body)  # noqa: E731
    r = await put("free", {"label": "Free", "included": ["vehicles", "billing"], "quotas": {}})
    assert r.status_code == 400 and "billing" in r.json()["detail"]
    r = await put("free", {"label": "Free", "included": ["vehicles", "scheduled_reports"], "quotas": {}})
    assert r.status_code == 400 and "scheduled_reports" in r.json()["detail"]
    r = await put("free", {"label": "Free", "included": ["nope"], "quotas": {}})
    assert r.status_code == 400
    r = await put("free", {"label": "Free", "included": ["*"], "quotas": {"max_ai": 3}})
    assert r.status_code == 400 and "max_ai" in r.json()["detail"]
    r = await put("free", {"label": "Free", "included": ["*"], "quotas": {"max_users": -1}})
    assert r.status_code == 400
    r = await put("Gold", {"label": "Gold", "included": ["*"], "quotas": {}})
    assert r.status_code == 400
    r = await put("free", {"label": "", "included": ["*"], "quotas": {}})
    assert r.status_code == 422
    r = await put("free", {"label": "   ", "included": ["*"], "quotas": {}})
    assert r.status_code == 400 and "Label" in r.json()["detail"]
    r = await put("nope", {"label": "Nope", "included": ["*"], "quotas": {}})
    assert r.status_code == 404                                # PUT never creates
    # nothing changed
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    assert {p["tier"]: p for p in g["plans"]}["free"]["included"] == ["*"]


@pytest.mark.asyncio
async def test_the_operator_names_a_plan_and_the_key_is_derived_for_them(system_app):
    """The key is the plan's identity — it keys five tables and rides
    into Stripe's lookup_key and metadata, and it cannot be corrected
    afterwards.  So it is DERIVED, never sent: a plan called Gold has
    been keyed "costum" since the day somebody typed it."""
    s = system_app
    post = lambda body: s["client"].post("/api/system/plans", headers=s["op"], json=body)  # noqa: E731
    r = await post({"label": "Gold"})
    assert r.status_code == 201
    assert r.json()["plan"]["tier"] == "gold", "derived from the name"
    assert r.json()["plan"]["included"] == ["*"] and r.json()["plan"]["accounts"] == 0
    rows = await s["db"].list_platform_audit(event="plan.created", limit=5)
    assert rows and '"tier": "gold"' in rows[0]["details"]

    # a key the caller sends is NOT honoured — the whole point
    r = await post({"label": "Silver", "tier": "anything_i_like"})
    assert r.status_code == 201 and r.json()["plan"]["tier"] == "silver"

    # narrow Gold, then create the same name again: it steps aside to
    # gold_2 rather than refusing, and the narrowing on gold stands
    r = await s["client"].put("/api/system/plans/gold", headers=s["op"],
                              json={"label": "Gold", "included": ["vehicles"], "quotas": {"max_users": 3}})
    assert r.status_code == 200
    r = await post({"label": "Gold"})
    assert r.status_code == 201 and r.json()["plan"]["tier"] == "gold_2"

    # a blank name is refused before any key is derived from nothing
    r = await post({"label": "  "})
    assert r.status_code == 400 and "Label is required" in r.json()["detail"]
    # and a name of pure punctuation still yields a usable key rather
    # than a dead end — the operator named something, after all
    r = await post({"label": "★★★"})
    assert r.status_code == 201
    from capabilities.permissions.plans import PLAN_KEY_RE
    assert PLAN_KEY_RE.match(r.json()["plan"]["tier"])

    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    gold = {p["tier"]: p for p in g["plans"]}["gold"]
    assert gold["included"] == ["vehicles"] and gold["quotas"] == {"max_users": 3} and gold["label"] == "Gold"


@pytest.mark.asyncio
async def test_everything_wins_over_a_list_and_a_stored_stray_id_reads_normalized(system_app):
    s = system_app
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["maintenance", "*"], "quotas": {}})
    assert r.status_code == 200 and r.json()["plan"]["included"] == ["*"]
    # a row edited behind the API's back names an id that is not a plan line
    await s["db"].upsert_plan("starter", label="Starter", included=["vehicles", "scheduled_reports", "billing"])
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    assert {p["tier"]: p for p in g["plans"]}["starter"]["included"] == ["vehicles"]


@pytest.mark.asyncio
async def test_the_price_catalog_round_trips_and_an_omitted_field_keeps_its_value(system_app):
    s = system_app
    r = await s["client"].put("/api/system/plans/starter", headers=s["op"],
                              json={"label": "Starter", "included": ["*"], "quotas": {},
                                    "price_monthly_cents": 5900, "stripe_price_id": " price_x ", "public": False, "sort": 7})
    assert r.status_code == 200, r.text
    p = r.json()["plan"]
    assert (p["price_monthly_cents"], p["stripe_price_id"], p["public"], p["sort"]) == (5900, "price_x", False, 7)
    assert p["base_vehicles"] == 10 and p["extra_vehicle_cents"] == 299       # untouched fields kept
    # a write that names no catalog field changes none of it
    r = await s["client"].put("/api/system/plans/starter", headers=s["op"],
                              json={"label": "Starter+", "included": ["*"], "quotas": {"max_users": 9}})
    p = r.json()["plan"]
    assert (p["label"], p["price_monthly_cents"], p["public"], p["sort"], p["quotas"]) == ("Starter+", 5900, False, 7, {"max_users": 9})
    r = await s["client"].put("/api/system/plans/starter", headers=s["op"],
                              json={"label": "Starter", "included": ["*"], "quotas": {}, "price_monthly_cents": -1})
    assert r.status_code == 422
    rows = await s["db"].list_platform_audit(event="plan.updated", limit=1)
    assert '"price_monthly_cents": 5900' in rows[0]["details"]


@pytest.mark.asyncio
async def test_the_operator_moves_an_account_to_a_plan_and_the_resolver_follows_at_once(system_app):
    from capabilities.permissions.plans import EXCLUDABLE, invalidate_plans
    from capabilities.permissions.roles import get_account_permissions
    s = system_app
    await s["db"].upsert_plan("starter", label="Starter", included=[i for i in EXCLUDABLE if i != "maintenance"])
    invalidate_plans()
    assert (await get_account_permissions(Role.OWNER, s["free"].id)).can_view_maintenance     # free = everything
    await s["db"].get_or_create_subscription(s["free"].id)                                     # the row the Billing page reads
    r = await s["client"].patch(f"/api/system/accounts/{s['free'].id}/plan", headers=s["op"],
                                json={"tier": "starter", "reason": "enterprise deal"})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": s["free"].id, "tier": "starter", "changed": True, "previous": "free"}
    assert (await s["db"].get_account(s["free"].id)).tier == "starter"
    assert not (await get_account_permissions(Role.OWNER, s["free"].id)).can_view_maintenance   # at once
    sub = await s["db"].get_subscription(s["free"].id)
    assert sub["tier"] == "starter" and sub["monthly_base_usd"] == 4900 and sub["base_vehicles"] == 10
    rows = await s["db"].list_platform_audit(event="account_plan", limit=1)
    assert rows and rows[0]["account_id"] == s["free"].id and '"to": "starter"' in rows[0]["details"]
    # the same plan again is a no-op; an unknown plan is refused
    assert (await s["client"].patch(f"/api/system/accounts/{s['free'].id}/plan", headers=s["op"], json={"tier": "starter"})).json()["changed"] is False
    assert (await s["client"].patch(f"/api/system/accounts/{s['free'].id}/plan", headers=s["op"], json={"tier": "gold"})).status_code == 404
    assert (await s["client"].patch(f"/api/system/accounts/{s['free'].id}/plan", headers=s["non_op"], json={"tier": "pro"})).status_code == 403


@pytest.mark.asyncio
async def test_an_account_stripe_bills_is_not_moved_by_hand(system_app):
    s = system_app
    await s["db"].get_or_create_subscription(s["pro"].id)
    await s["db"].update_subscription(s["pro"].id, provider="stripe", provider_subscription_id="sub_live", status="active")
    r = await s["client"].patch(f"/api/system/accounts/{s['pro'].id}/plan", headers=s["op"], json={"tier": "starter"})
    assert r.status_code == 409 and "Stripe" in r.json()["detail"]
    assert (await s["db"].get_account(s["pro"].id)).tier == "pro"


@pytest.mark.asyncio
async def test_the_trial_flag_moves_between_plans_from_the_panel(system_app):
    s = system_app
    r = await s["client"].put("/api/system/plans/starter", headers=s["op"],
                              json={"label": "Starter", "included": ["*"], "quotas": {}, "trial_default": True})
    assert r.status_code == 200 and r.json()["plan"]["trial_default"] is True
    g = {p["tier"]: p for p in (await s["client"].get("/api/system/plans", headers=s["op"])).json()["plans"]}
    assert g["starter"]["trial_default"] is True and g["pro"]["trial_default"] is False
    assert await s["db"].trial_plan() == "starter"


def _fake_stripe(subs=None, price_amount=12900):
    """Just enough Stripe for a save and a rollout: Product/Price creation,
    Price retrieve/modify, Subscription retrieve/modify.  Records calls."""
    calls = []
    subs = subs or {}
    price = {"id": "price_new", "active": True, "unit_amount": price_amount, "currency": "usd",
             "recurring": {"interval": "month", "interval_count": 1}}
    # the plan's extras Price, made on the same save; Stripe answers for it by id
    x_price = {"id": "price_x_new", "active": True, "unit_amount": 299, "currency": "usd",
               "recurring": {"interval": "month", "interval_count": 1}, "metadata": {"kind": "extra"}}

    class _S:
        class Product:
            @staticmethod
            def create(**kw):
                calls.append(("Product.create", kw))
                return {"id": "prod_x_new" if (kw.get("metadata") or {}).get("kind") == "extra" else "prod_new"}
            @staticmethod
            def modify(pid, **kw): calls.append(("Product.modify", pid, kw)); return {"id": pid, **kw}
        class Price:
            @staticmethod
            def create(**kw):
                calls.append(("Price.create", kw))
                return {"id": "price_x_new" if (kw.get("metadata") or {}).get("kind") == "extra" else "price_new"}
            @staticmethod
            def retrieve(pid):
                calls.append(("Price.retrieve", pid))
                return x_price if pid == "price_x_new" else price
            @staticmethod
            def modify(pid, **kw): calls.append(("Price.modify", pid, kw)); return {"id": pid}
        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None): calls.append(("Subscription.retrieve", sid)); return subs[sid]
            @staticmethod
            def modify(sid, **kw):
                calls.append(("Subscription.modify", sid, kw))
                s = dict(subs[sid])
                moved = {it["id"]: it["price"] for it in kw["items"]}
                data = []
                for it in s["items"]["data"]:
                    if it["id"] in moved:
                        pr = x_price if moved[it["id"]] == "price_x_new" else price
                        data.append({"id": it["id"], "price": {**pr, "id": moved[it["id"]]}})
                    else:
                        data.append(it)
                s["items"] = {"data": data}
                subs[sid] = s
                return s
    _S.calls = calls
    _S.price = price
    return _S


@pytest.mark.asyncio
async def test_in_stub_mode_a_price_change_creates_nothing_and_a_rollout_is_refused(system_app, monkeypatch):
    s = system_app
    monkeypatch.setenv("BILLING_PROVIDER", "stub")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "price_monthly_cents": 12900})
    assert r.status_code == 200, r.text
    assert r.json()["plan"]["price_monthly_cents"] == 12900 and r.json()["plan"]["stripe_price_id"] == ""
    assert r.json()["subscribers_on_old_price"] == 0
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    assert g["billing_provider"] == "stub"
    assert set(g["stripe_setup"]) == {"secret_key", "webhook_secret", "extras_price", "return_url"}
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x"); monkeypatch.delenv("STRIPE_PRICE_EXTRA_VEHICLE", raising=False)
    g2 = (await s["client"].get("/api/system/plans", headers=s["op"])).json()["stripe_setup"]
    assert g2["secret_key"] is True and g2["extras_price"] is False
    r = await s["client"].post("/api/system/plans/pro/rollout", headers=s["op"])
    assert r.status_code == 400 and "stub" in r.json()["detail"]


@pytest.mark.asyncio
async def test_in_stripe_mode_the_save_makes_the_price_and_the_rollout_moves_the_subscribers(system_app, monkeypatch):
    s = system_app
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_PRICE_EXTRA_VEHICLE", "price_extra")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    # two live Stripe subscribers on Pro, one on the old price, one already on the new
    subs = {}
    for n, (tg, sub_id, base_price) in enumerate([(915001, "sub_1", "price_old"), (915002, "sub_2", "price_new")]):
        acct = await s["db"].create_account(f"Rollout Co {n}", tier="pro")
        await s["db"].get_or_create_subscription(acct.id)
        await s["db"].update_subscription(acct.id, tier="pro", provider="stripe", provider_subscription_id=sub_id,
                                          status="active", provider_base_price_id=base_price)
        # sub_2 is on the new base AND the new extras Price already; sub_1 on the old base and the env extras
        x_id, x_meta = ("price_x_new", {"kind": "extra"}) if base_price == "price_new" else ("price_extra", {})
        subs[sub_id] = {"id": sub_id, "status": "active", "current_period_end": 1800000000,
                        "items": {"data": [{"id": f"si_{sub_id}", "price": {"id": base_price, "unit_amount": 9900 if base_price == "price_old" else 12900,
                                                                            "currency": "usd", "recurring": {"interval": "month", "interval_count": 1}}},
                                           {"id": f"si_x_{sub_id}", "price": {"id": x_id, "unit_amount": 299, "currency": "usd", "metadata": x_meta,
                                                                              "recurring": {"interval": "month", "interval_count": 1}}}]}}
    fake = _fake_stripe(subs)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    # the save: Stripe first, then the row; the ids land on the row; the old price is archived
    await s["db"].upsert_plan("pro", label="Pro", included=["*"], stripe_price_id="price_old")
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "price_monthly_cents": 12900,
                                    "extra_vehicle_cents": 299})
    assert r.status_code == 200, r.text
    p = r.json()["plan"]
    assert (p["price_monthly_cents"], p["stripe_price_id"], p["stripe_product_id"]) == (12900, "price_new", "prod_new")
    assert p["stripe_extra_price_id"] == "price_x_new", "the extras Price is made on the same save"
    assert p["stripe_extra_product_id"] == "prod_x_new", "and on a Product of its own, so the bill reads right"
    assert r.json()["subscribers_on_old_price"] == 1
    names = [c[0] for c in fake.calls]
    # the base pair, then the extras pair — the extras line is named after
    # a Product of its own so a bill does not print "Pro" twice
    assert names[:4] == ["Product.create", "Price.create", "Product.create", "Price.create"]
    assert ("Price.modify", "price_old", {"active": False}) in fake.calls
    assert fake.calls[1][1]["unit_amount"] == 12900 and fake.calls[1][1]["idempotency_key"].startswith("plan-price:pro:12900:")
    assert fake.calls[2][1]["name"] == "Pro — extra trucks"
    assert fake.calls[3][1]["unit_amount"] == 299 and fake.calls[3][1]["product"] == "prod_x_new"
    assert fake.calls[3][1]["metadata"] == {"tier": "pro", "kind": "extra"}
    # a pasted price id is refused in Stripe mode — the Price is made on save, never set by hand
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "stripe_price_id": "price_pasted"})
    assert r.status_code == 400 and "cannot be set by hand" in r.json()["detail"]
    assert (await s["db"].get_plan("pro"))["stripe_price_id"] == "price_new"
    # saving again with the SAME prices creates nothing
    n_before = len(fake.calls)
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "price_monthly_cents": 12900,
                                    "extra_vehicle_cents": 299})
    assert r.status_code == 200 and len(fake.calls) == n_before
    # preview, then the rollout: the old-price subscriber moves with no proration; the other is "already"
    pv = (await s["client"].get("/api/system/plans/pro/rollout", headers=s["op"])).json()
    assert (pv["candidates"], pv["to_move"], pv["already_on_new_price"], pv["to_cents"]) == (2, 1, 1, 12900)
    r = await s["client"].post("/api/system/plans/pro/rollout", headers=s["op"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["counts"] == {"changed": 1, "already": 1} and out["remaining"] == 0 and out["finished"] is True
    mods = [c for c in fake.calls if c[0] == "Subscription.modify"]
    assert len(mods) == 1 and mods[0][1] == "sub_1"
    # the extras item, on the env-wide Price from before, moves in the same call
    assert mods[0][2]["items"] == [{"id": "si_sub_1", "price": "price_new", "quantity": 1},
                                   {"id": "si_x_sub_1", "price": "price_x_new", "quantity": 1}]
    assert mods[0][2]["proration_behavior"] == "none"
    moved = [x for x in await s["db"].subscriptions_on_tier("pro") if x["provider_subscription_id"] == "sub_1"][0]
    assert moved["provider_base_price_id"] == "price_new" and moved["monthly_base_usd"] == 12900
    rows = await s["db"].list_platform_audit(event="plan.rollout", limit=1)
    assert rows and '"finished": true' in rows[0]["details"]
    assert (await s["client"].get("/api/system/plans/pro/rollout", headers=s["op"])).json()["to_move"] == 0
    # a price that Stripe does not agree with refuses the rollout
    fake.price["unit_amount"] = 9900
    r = await s["client"].post("/api/system/plans/pro/rollout", headers=s["op"])
    assert r.status_code == 400 and "save the plan again" in r.json()["detail"]
    assert (await s["client"].post("/api/system/plans/pro/rollout", headers=s["non_op"])).status_code == 403


@pytest.mark.asyncio
async def test_accounts_on_a_plan_with_no_row_are_named(system_app):
    s = system_app
    await s["db"].update_account_tier(s["pro"].id, "legacy_gold")
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    assert g["accounts_without_plan"] == {"legacy_gold": 1}


@pytest.mark.asyncio
async def test_a_save_against_the_other_modes_ids_says_which_runbook_step_fixes_it(system_app, monkeypatch):
    """Live keys, sandbox ids on the row (the SQL step skipped): Stripe
    answers "No such product".  The operator must read the cause and the
    fix — as a 409 the console shows, not a 502 nginx may replace."""
    s = system_app
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    fake = _fake_stripe()
    def _no_such_product(**kw):
        raise RuntimeError("No such product: 'prod_sandbox'")
    fake.Price.create = staticmethod(_no_such_product)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    await s["db"].upsert_plan("pro", label="Pro", included=["*"], price_monthly_cents=9900,
                              stripe_price_id="price_sandbox", stripe_product_id="prod_sandbox")
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {},
                                    "price_monthly_cents": 9900, "extra_vehicle_cents": 299})
    assert r.status_code == 409, r.text
    assert "other Stripe mode" in r.json()["detail"] and "4b step 2" in r.json()["detail"]
    assert (await s["db"].get_plan("pro"))["stripe_extra_price_id"] == "", "nothing was written"


@pytest.mark.asyncio
async def test_renaming_a_plan_carries_the_new_name_onto_the_customers_invoice(system_app, monkeypatch):
    """The words on a checkout page and an invoice live on the Stripe
    Product.  A plan renamed here kept its old name there — the customer
    paid for "Gold" long after the console said "Premier"."""
    s = system_app
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    fake = _fake_stripe()
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    await s["db"].upsert_plan("gold", label="Gold", included=["*"], price_monthly_cents=15000,
                              extra_vehicle_cents=499, stripe_price_id="price_g",
                              stripe_product_id="prod_g", stripe_extra_price_id="price_gx",
                              stripe_extra_product_id="prod_gx")
    r = await s["client"].put("/api/system/plans/gold", headers=s["op"],
                              json={"label": "Premier", "included": ["*"], "quotas": {}})
    assert r.status_code == 200, r.text
    renames = [(c[1], c[2]["name"]) for c in fake.calls if c[0] == "Product.modify"]
    assert renames == [("prod_g", "Premier"), ("prod_gx", "Premier — extra trucks")]
    # saving without changing the label renames nothing
    n = len([c for c in fake.calls if c[0] == "Product.modify"])
    await s["client"].put("/api/system/plans/gold", headers=s["op"],
                          json={"label": "Premier", "included": ["*"], "quotas": {}})
    assert len([c for c in fake.calls if c[0] == "Product.modify"]) == n


@pytest.mark.asyncio
async def test_a_save_heals_a_plan_whose_extras_price_predates_its_own_product(system_app, monkeypatch):
    """The plans saved before the extras Product existed carry a Price on
    the plan's own Product — the shape that printed the plan's name twice
    on a bill.  A Save must remake it, or the operator is sent to SQL."""
    s = system_app
    monkeypatch.setenv("BILLING_PROVIDER", "stripe")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    import capabilities.platform.billing as _b
    monkeypatch.setattr(_b, "_provider", None)
    fake = _fake_stripe()
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    # the old shape: an extras Price, no extras Product
    await s["db"].upsert_plan("gold", label="Gold", included=["*"], price_monthly_cents=15000,
                              extra_vehicle_cents=499, stripe_price_id="price_g",
                              stripe_product_id="prod_g", stripe_extra_price_id="price_gx_old")
    r = await s["client"].put("/api/system/plans/gold", headers=s["op"],
                              json={"label": "Gold", "included": ["*"], "quotas": {},
                                    "price_monthly_cents": 15000, "extra_vehicle_cents": 499})
    assert r.status_code == 200, r.text
    row = await s["db"].get_plan("gold")
    assert row["stripe_extra_product_id"] == "prod_x_new", "a Product of its own was made"
    assert row["stripe_extra_price_id"] == "price_x_new" and row["stripe_price_id"] == "price_g", \
        "the extras Price moved; the base Price was already right and was left alone"
    assert ("Price.modify", "price_gx_old", {"active": False}) in fake.calls, "the old extras Price is archived"
    # and a second Save, now in the new shape, creates nothing
    n = len(fake.calls)
    await s["client"].put("/api/system/plans/gold", headers=s["op"],
                          json={"label": "Gold", "included": ["*"], "quotas": {},
                                "price_monthly_cents": 15000, "extra_vehicle_cents": 499})
    assert len(fake.calls) == n


# ── deleting a plan ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_plan_an_account_sits_on_cannot_be_deleted(system_app):
    """Not a downgrade — a closure.  A tier with no plan row resolves to
    None in permissions.plans, and plan_includes then answers False for
    every sellable feature, so the page, the API, the bot and the AI all
    shut on that account at once."""
    s = system_app
    # a plan that is NOT the trial default, so this test measures the
    # dependent count and not a different guard firing first
    await s["db"].upsert_plan("occupied", label="Occupied", included=["*"])
    acct = await s["db"].create_account("Sitting Co", tier="occupied")
    await s["db"].get_or_create_subscription(acct.id)
    await s["db"].update_subscription(acct.id, tier="occupied")

    r = await s["client"].delete("/api/system/plans/occupied", headers=s["op"])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "still point at this plan" in detail
    assert "every service away" in detail, "the refusal says what it would have cost"
    assert await s["db"].get_plan("occupied"), "and the plan is untouched"


@pytest.mark.asyncio
async def test_the_floor_and_the_trial_default_are_never_deletable(system_app):
    s = system_app
    r = await s["client"].delete("/api/system/plans/free", headers=s["op"])
    assert r.status_code == 409 and "falls back to" in r.json()["detail"]

    await s["db"].upsert_plan("trialy", label="Trialy", included=["*"], trial_default=True)
    r = await s["client"].delete("/api/system/plans/trialy", headers=s["op"])
    assert r.status_code == 409 and "trial default" in r.json()["detail"]
    assert await s["db"].get_plan("trialy")


@pytest.mark.asyncio
async def test_an_empty_plan_goes_and_takes_its_offers_with_it(system_app):
    s = system_app
    other = await s["db"].create_account("Offered Co", tier="free")
    await s["db"].upsert_plan("spare", label="Spare", included=["*"], price_monthly_cents=100)
    await s["db"].offer_plan("spare", other.id, created_by="operator:1")
    assert await s["db"].plan_offered_to("spare", other.id)

    r = await s["client"].delete("/api/system/plans/spare", headers=s["op"])
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == "spare" and r.json()["offers_withdrawn"] == 1
    assert await s["db"].get_plan("spare") is None
    assert not await s["db"].plan_offered_to("spare", other.id), "the offer went with it"
    rows = await s["db"].list_platform_audit(event="plan.deleted", limit=3)
    assert rows and '"tier": "spare"' in rows[0]["details"]

    assert (await s["client"].delete("/api/system/plans/spare", headers=s["op"])).status_code == 404


@pytest.mark.asyncio
async def test_an_open_customer_case_holds_the_plan_open_too(system_app):
    """A plan request is a conversation in progress; deleting the plan
    under it leaves the customer's case pointing at nothing."""
    s = system_app
    acct = await s["db"].create_account("Asking Co", tier="free")
    await s["db"].upsert_plan("asked", label="Asked", included=["*"])
    await s["db"].create_plan_request(acct.id, tier="asked", note="how much?",
                                      contact_email="a@b.com")

    r = await s["client"].delete("/api/system/plans/asked", headers=s["op"])
    assert r.status_code == 409 and "open request" in r.json()["detail"]
    assert await s["db"].get_plan("asked")


@pytest.mark.asyncio
async def test_a_dependent_count_we_cannot_read_refuses_rather_than_guesses(system_app, monkeypatch):
    """The counts are the only thing standing between a delete and an
    account losing every service.  A table we cannot read might be
    holding one, so an unreadable count is a refusal, never a zero."""
    s = system_app
    await s["db"].upsert_plan("unknowable", label="Unknowable", included=["*"])

    async def _blind(tier):
        return {"accounts": 0, "subscriptions": -1, "offers": 0, "open_requests": 0}
    monkeypatch.setattr(s["db"], "plan_dependents", _blind)

    r = await s["client"].delete("/api/system/plans/unknowable", headers=s["op"])
    assert r.status_code == 503
    assert "subscriptions" in r.json()["detail"]
    assert "refusing rather than guessing" in r.json()["detail"]
    assert await s["db"].get_plan("unknowable"), "and nothing was deleted"


# ── retiring: closed to new accounts, untouched for the old ────────

@pytest.mark.asyncio
async def test_retiring_closes_every_door_and_leaves_the_accounts_alone(system_app):
    """The state between "we stopped selling this" and "nobody is left
    on it".  A plan with customers can only be sunset — deleting one
    they sit on resolves their tier to nothing and closes the product."""
    s = system_app
    await s["db"].upsert_plan("sunset", label="Sunset", included=["*"],
                              price_monthly_cents=5000, public=True)
    staying = await s["db"].create_account("Staying Co", tier="sunset")
    await s["db"].get_or_create_subscription(staying.id)
    await s["db"].update_subscription(staying.id, tier="sunset")
    newcomer = await s["db"].create_account("Newcomer Co", tier="free")

    r = await s["client"].post("/api/system/plans/sunset/retire", headers=s["op"],
                               json={"retired": True})
    assert r.status_code == 200, r.text
    assert r.json()["retired"] is True and r.json()["accounts_still_on_it"] == 1

    # door 1 — checkout and the customer's plan list, through the one gate
    from capabilities.platform.billing.offers import purchasable_plan
    assert await purchasable_plan(s["db"], "sunset", newcomer.id) is None
    # door 2 — an operator moving an account by hand
    r = await s["client"].patch(f"/api/system/accounts/{newcomer.id}/plan",
                                headers=s["op"], json={"tier": "sunset"})
    assert r.status_code == 409 and "retired" in r.json()["detail"]
    # door 3 — an offer
    from capabilities.platform.billing.offers import offer_refusal
    plan = await s["db"].get_plan("sunset")
    assert "retired" in offer_refusal(plan, [plan], provider="stub")
    # door 4 — a trial
    assert await s["db"].trial_plan() != "sunset"
    # door 5 — the save path putting it back on sale sideways
    r = await s["client"].put("/api/system/plans/sunset", headers=s["op"],
                              json={"label": "Sunset", "included": ["*"], "quotas": {},
                                    "public": True})
    assert r.status_code == 409 and "Un-retire it first" in r.json()["detail"]

    # and the account already on it is untouched
    acct = await s["db"].get_account(staying.id)
    assert acct.tier == "sunset"
    sub = await s["db"].get_subscription(staying.id)
    assert sub["tier"] == "sunset"


@pytest.mark.asyncio
async def test_a_retired_plan_stays_on_the_page_of_the_account_that_is_on_it(system_app):
    """Their own plan is the one thing on that page they need to read.
    Hiding it would tell a paying customer their plan had vanished."""
    s = system_app
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    await s["db"].upsert_plan("sunset2", label="Sunset Two", included=["*"],
                              price_monthly_cents=5000, public=True)
    mine = await s["db"].create_account("On It Co", tier="sunset2")
    other = await s["db"].create_account("Not On It Co", tier="free")
    owner = await s["db"].create_user(980001, mine.id, role=Role.OWNER)
    stranger = await s["db"].create_user(980002, other.id, role=Role.OWNER)
    await s["client"].post("/api/system/plans/sunset2/retire", headers=s["op"],
                           json={"retired": True})

    def hdr(u, a):
        return {"Authorization": f"Bearer {create_jwt(u.telegram_id, a.id, 'owner')}"}
    seen = (await s["client"].get("/api/billing/plans", headers=hdr(owner, mine))).json()
    tiers = {p["tier"]: p for p in seen["plans"]}
    assert "sunset2" in tiers and tiers["sunset2"]["current"] is True

    theirs = (await s["client"].get("/api/billing/plans", headers=hdr(stranger, other))).json()
    assert "sunset2" not in {p["tier"] for p in theirs["plans"]}, "not a choice for anyone else"


@pytest.mark.asyncio
async def test_the_trial_default_cannot_be_retired_and_un_retiring_reopens(system_app):
    s = system_app
    await s["db"].upsert_plan("trialish", label="Trialish", included=["*"], trial_default=True)
    r = await s["client"].post("/api/system/plans/trialish/retire", headers=s["op"],
                               json={"retired": True})
    assert r.status_code == 409 and "trial default" in r.json()["detail"]

    await s["db"].upsert_plan("reopen", label="Reopen", included=["*"],
                              price_monthly_cents=100, public=True)
    acct = await s["db"].create_account("Reopen Co", tier="free")
    await s["client"].post("/api/system/plans/reopen/retire", headers=s["op"], json={"retired": True})
    from capabilities.platform.billing.offers import purchasable_plan
    assert await purchasable_plan(s["db"], "reopen", acct.id) is None
    r = await s["client"].post("/api/system/plans/reopen/retire", headers=s["op"],
                               json={"retired": False})
    assert r.status_code == 200 and r.json()["retired"] is False
    assert await purchasable_plan(s["db"], "reopen", acct.id) is not None, "back on sale"
