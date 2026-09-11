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
async def test_the_operator_creates_a_plan_and_a_taken_key_is_refused(system_app):
    s = system_app
    post = lambda body: s["client"].post("/api/system/plans", headers=s["op"], json=body)  # noqa: E731
    r = await post({"tier": "gold", "label": "Gold"})
    assert r.status_code == 201
    assert r.json()["plan"]["included"] == ["*"] and r.json()["plan"]["accounts"] == 0
    rows = await s["db"].list_platform_audit(event="plan.created", limit=5)
    assert rows and '"tier": "gold"' in rows[0]["details"]
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    assert "gold" in {p["tier"] for p in g["plans"]}
    assert g["plan_key_pattern"]

    # narrow it, then "create" it again by mistake: refused, and the narrowing stands
    r = await s["client"].put("/api/system/plans/gold", headers=s["op"],
                              json={"label": "Gold", "included": ["vehicles"], "quotas": {"max_users": 3}})
    assert r.status_code == 200
    r = await post({"tier": "gold", "label": "Gold again"})
    assert r.status_code == 409
    r = await post({"tier": "Gold", "label": "Gold"})
    assert r.status_code == 400
    r = await post({"tier": "silver", "label": "  "})
    assert r.status_code == 400
    g = (await s["client"].get("/api/system/plans", headers=s["op"])).json()
    gold = {p["tier"]: p for p in g["plans"]}["gold"]
    assert gold["included"] == ["vehicles"] and gold["quotas"] == {"max_users": 3} and gold["label"] == "Gold"
    assert (await post({"tier": "silver", "label": "Silver"})).status_code == 201


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

    class _S:
        class Product:
            @staticmethod
            def create(**kw): calls.append(("Product.create", kw)); return {"id": "prod_new"}
        class Price:
            @staticmethod
            def create(**kw): calls.append(("Price.create", kw)); return {"id": "price_new"}
            @staticmethod
            def retrieve(pid): calls.append(("Price.retrieve", pid)); return price
            @staticmethod
            def modify(pid, **kw): calls.append(("Price.modify", pid, kw)); return {"id": pid}
        class Subscription:
            @staticmethod
            def retrieve(sid, expand=None): calls.append(("Subscription.retrieve", sid)); return subs[sid]
            @staticmethod
            def modify(sid, **kw):
                calls.append(("Subscription.modify", sid, kw))
                s = dict(subs[sid]); base = kw["items"][0]
                s["items"] = {"data": [{"id": base["id"], "price": {"id": base["price"], "unit_amount": price_amount, "currency": "usd",
                                                                    "recurring": {"interval": "month", "interval_count": 1}}}]}
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
        subs[sub_id] = {"id": sub_id, "status": "active", "current_period_end": 1800000000,
                        "items": {"data": [{"id": f"si_{sub_id}", "price": {"id": base_price, "unit_amount": 9900 if base_price == "price_old" else 12900,
                                                                            "currency": "usd", "recurring": {"interval": "month", "interval_count": 1}}},
                                           {"id": f"si_x_{sub_id}", "price": {"id": "price_extra", "unit_amount": 299, "currency": "usd",
                                                                              "recurring": {"interval": "month", "interval_count": 1}}}]}}
    fake = _fake_stripe(subs)
    monkeypatch.setattr("capabilities.platform.billing.stripe_client._stripe", lambda: fake)
    # the save: Stripe first, then the row; the ids land on the row; the old price is archived
    await s["db"].upsert_plan("pro", label="Pro", included=["*"], stripe_price_id="price_old")
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "price_monthly_cents": 12900})
    assert r.status_code == 200, r.text
    p = r.json()["plan"]
    assert (p["price_monthly_cents"], p["stripe_price_id"], p["stripe_product_id"]) == (12900, "price_new", "prod_new")
    assert r.json()["subscribers_on_old_price"] == 1
    names = [c[0] for c in fake.calls]
    assert names[:2] == ["Product.create", "Price.create"] and ("Price.modify", "price_old", {"active": False}) in fake.calls
    assert fake.calls[1][1]["unit_amount"] == 12900 and fake.calls[1][1]["idempotency_key"].startswith("plan-price:pro:12900:")
    # a pasted price id is refused in Stripe mode — the Price is made on save, never set by hand
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "stripe_price_id": "price_pasted"})
    assert r.status_code == 400 and "cannot be set by hand" in r.json()["detail"]
    assert (await s["db"].get_plan("pro"))["stripe_price_id"] == "price_new"
    # saving again with the SAME price creates nothing
    n_before = len(fake.calls)
    r = await s["client"].put("/api/system/plans/pro", headers=s["op"],
                              json={"label": "Pro", "included": ["*"], "quotas": {}, "price_monthly_cents": 12900})
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
    assert mods[0][2]["items"] == [{"id": "si_sub_1", "price": "price_new", "quantity": 1}]
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
