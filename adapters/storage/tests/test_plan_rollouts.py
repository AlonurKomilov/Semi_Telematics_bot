"""The rollout tables: one row per rollout, one per account it touched,
a resumable open rollout, and the candidates from OUR subscriptions."""

import pytest


@pytest.mark.asyncio
async def test_candidates_are_live_stripe_subscriptions_on_the_tier(db):
    a = await db.create_account("R Live Co", tier="pro"); b = await db.create_account("R Stub Co", tier="pro")
    c = await db.create_account("R Dead Co", tier="pro"); d = await db.create_account("R Other Co", tier="starter")
    for acct in (a, b, c, d):
        await db.get_or_create_subscription(acct.id)
    await db.update_subscription(a.id, tier="pro", provider="stripe", provider_subscription_id="sub_a", status="active")
    await db.update_subscription(b.id, tier="pro", provider="stub", provider_subscription_id="sub_b", status="active")
    await db.update_subscription(c.id, tier="pro", provider="stripe", provider_subscription_id="sub_c", status="canceled")
    await db.update_subscription(d.id, tier="starter", provider="stripe", provider_subscription_id="sub_d", status="active")
    rows = await db.subscriptions_on_tier("pro")
    assert [r["account_id"] for r in rows] == [a.id]


@pytest.mark.asyncio
async def test_a_comped_account_is_not_a_candidate(db):
    a = await db.create_account("R Comped Co", tier="pro")
    await db.get_or_create_subscription(a.id)
    await db.update_subscription(a.id, tier="pro", provider="stripe", provider_subscription_id="sub_comp", status="active")
    assert [r["account_id"] for r in await db.subscriptions_on_tier("pro")] == [a.id]
    await db._db.execute("UPDATE subscriptions SET is_comped = 1 WHERE account_id = ?", (a.id,)); await db._db.commit()
    assert await db.subscriptions_on_tier("pro") == []


@pytest.mark.asyncio
async def test_one_open_rollout_per_plan_a_claim_per_batch_and_a_heartbeat_that_keeps_it_alive(db):
    rid = await db.create_price_rollout("pro", from_price_id="", to_price_id="price_new", from_cents=0, to_cents=1, actor="a")
    # a second operator's open does not fork: the same rollout comes back
    again = await db.create_price_rollout("pro", from_price_id="", to_price_id="price_new", from_cents=0, to_cents=1, actor="b")
    assert again == rid
    assert len([r for r in await db.list_price_rollouts("pro") if r["finished_at"] is None]) == 1
    # a claim is exclusive while held, released by its holder only, then free again
    assert await db.claim_rollout(rid, "t1") is True
    assert await db.claim_rollout(rid, "t2") is False
    await db.release_rollout(rid, "t2")                                   # not the holder: no effect
    assert await db.claim_rollout(rid, "t3") is False
    await db.release_rollout(rid, "t1")
    assert await db.claim_rollout(rid, "t3") is True
    await db.release_rollout(rid, "t3")
    # a fresh heartbeat keeps a long rollout alive; only an untouched one goes stale
    assert await db.abort_stale_rollouts("pro", older_than_minutes=15) == 0            # just released → alive
    await db._db.execute("UPDATE plan_price_rollouts SET last_batch_at = '2000-01-01T00:00:00+00:00', started_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (rid,))
    await db._db.commit()
    assert await db.abort_stale_rollouts("pro", older_than_minutes=15) == 1
    assert await db.open_price_rollout("pro") is None
    # a stale claim (a crashed batch) is taken over
    rid2 = await db.create_price_rollout("pro", from_price_id="", to_price_id="price_new", from_cents=0, to_cents=1, actor="a")
    assert await db.claim_rollout(rid2, "dead") is True
    await db._db.execute("UPDATE plan_price_rollouts SET running_since = '2000-01-01T00:00:00+00:00' WHERE id = ?", (rid2,))
    await db._db.commit()
    assert await db.claim_rollout(rid2, "alive") is True


@pytest.mark.asyncio
async def test_a_rollout_is_recorded_item_by_item_and_finished_once(db):
    rid = await db.create_price_rollout("pro", from_price_id="price_old", to_price_id="price_new",
                                        from_cents=9900, to_cents=12900, actor="tg:1")
    assert rid > 0
    assert (await db.open_price_rollout("pro"))["id"] == rid
    assert await db.open_price_rollout("starter") is None
    await db.record_rollout_item(rid, 1, subscription_id="sub_1", outcome="changed", effective_at="1800000000")
    await db.record_rollout_item(rid, 2, subscription_id="sub_2", outcome="error", error="boom")
    await db.record_rollout_item(rid, 2, subscription_id="sub_2", outcome="changed")          # a retry replaces the row
    items = {i["account_id"]: i for i in await db.rollout_items(rid)}
    assert items[1]["outcome"] == "changed" and items[2]["outcome"] == "changed" and items[2]["error"] == ""
    await db.finish_price_rollout(rid, summary={"changed": 2})
    assert await db.open_price_rollout("pro") is None
    recent = await db.list_price_rollouts("pro")
    assert recent[0]["id"] == rid and recent[0]["summary"] == {"changed": 2} and recent[0]["aborted"] == 0
    # a base price id rides the subscription row now
    a = await db.create_account("R Price Co", tier="pro")
    await db.get_or_create_subscription(a.id)
    await db.update_subscription(a.id, provider_base_price_id="price_new")
    assert (await db.get_subscription(a.id))["provider_base_price_id"] == "price_new"
    # the plan row remembers its Stripe Product
    await db.upsert_plan("pro", label="Pro", included=["*"], stripe_product_id="prod_x")
    assert (await db.get_plan("pro"))["stripe_product_id"] == "prod_x"
    await db.upsert_plan("pro", label="Pro", included=["*"])
    assert (await db.get_plan("pro"))["stripe_product_id"] == "prod_x"                      # None keeps it
