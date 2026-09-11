"""``plans`` — the table, its seed, and the mixin over it.

The seed puts every tier on ``["*"]`` so the mask's arrival changes
nothing; a re-run never rewrites a row the operator has narrowed.
"""

import pytest

from adapters.storage.platform_migrations import migrate_plans

TIERS = ("free", "starter", "pro", "enterprise")


@pytest.mark.asyncio
async def test_seed_puts_every_tier_on_everything(db):
    rows = {r["tier"]: r for r in await db.list_plans()}
    assert set(TIERS) <= set(rows)
    for t in TIERS:
        assert rows[t]["included"] == ["*"], t
        assert rows[t]["quotas"] == {}
        assert rows[t]["label"]
        assert rows[t]["updated_at"]


@pytest.mark.asyncio
async def test_a_tier_an_account_already_carries_gets_a_row_too(db):
    """The mask is fail-closed: a tier with no row would hold nothing
    sellable, so the seed covers every value the accounts table has."""
    await db._db.execute(
        "INSERT INTO accounts (name, slug, tier, created_at) VALUES (?, ?, ?, ?)",
        ("Legacy Co", "legacy-co", "legacy_gold", "2026-01-01T00:00:00+00:00"))
    await db._db.commit()
    await migrate_plans(db._db)
    row = await db.get_plan("legacy_gold")
    assert row and row["included"] == ["*"] and row["label"] == "Legacy Gold"


@pytest.mark.asyncio
async def test_the_catalog_columns_are_seeded_from_the_code_table_and_never_rewritten(db):
    from adapters.storage.platform_migrations import migrate_plans_catalog
    rows = {r["tier"]: r for r in await db.list_plans()}
    assert (rows["starter"]["price_monthly_cents"], rows["starter"]["base_vehicles"], rows["starter"]["extra_vehicle_cents"]) == (4900, 10, 299)
    assert (rows["pro"]["price_monthly_cents"], rows["pro"]["public"], rows["pro"]["sort"]) == (9900, True, 2)
    assert rows["starter"]["public"] is True and rows["free"]["public"] is False and rows["enterprise"]["public"] is False
    assert rows["free"]["sort"] == 0 and rows["enterprise"]["sort"] == 3 and rows["starter"]["stripe_price_id"] == ""
    # the operator prices Pro; a re-run keeps it
    await db.upsert_plan("pro", label="Pro", included=["*"], price_monthly_cents=12900, stripe_price_id="price_abc", public=False, sort=9)
    await migrate_plans_catalog(db._db)
    pro = await db.get_plan("pro")
    assert (pro["price_monthly_cents"], pro["stripe_price_id"], pro["public"], pro["sort"]) == (12900, "price_abc", False, 9)
    assert pro["base_vehicles"] == 10                          # a field left None keeps the row's value


@pytest.mark.asyncio
async def test_pricing_reads_the_row_first_and_the_code_table_when_the_row_is_unpriced(db):
    from adapters.storage.billing import BillingMixin
    assert await db.pricing_for("starter") == {"tier": "starter", "base_vehicles": 10, "monthly_base_cents": 4900, "extra_vehicle_cents": 299}
    await db.upsert_plan("starter", label="Starter", included=["*"], price_monthly_cents=5900, base_vehicles=12, extra_vehicle_cents=349)
    assert await db.pricing_for("starter") == {"tier": "starter", "base_vehicles": 12, "monthly_base_cents": 5900, "extra_vehicle_cents": 349}
    # an unpriced row (a plan the operator just created) → the code table's answer for that key
    await db.upsert_plan("gold", label="Gold", included=["*"])
    assert await db.pricing_for("gold") == BillingMixin.tier_pricing("gold")
    assert await db.pricing_for("nope") == BillingMixin.tier_pricing("nope")


@pytest.mark.asyncio
async def test_one_plan_carries_the_trial_and_a_trial_with_no_plan_flagged_does_not_start(db):
    assert await db.trial_plan() == "pro"                       # the seed: Pro, as the signup code had it
    await db.upsert_plan("starter", label="Starter", included=["*"], trial_default=True)
    assert await db.trial_plan() == "starter"
    assert (await db.get_plan("pro"))["trial_default"] is False    # one row carries it
    acct = await db.create_account("Trial Co")
    ends = await db.start_trial(acct.id, days=14)                 # no tier named → the flagged plan
    assert ends and (await db.get_account(acct.id)).tier == "starter"
    await db.upsert_plan("starter", label="Starter", included=["*"], trial_default=False)
    assert await db.trial_plan() is None
    acct2 = await db.create_account("No Trial Co")
    assert await db.start_trial(acct2.id, days=14) is None
    assert (await db.get_account(acct2.id)).tier == "free"


@pytest.mark.asyncio
async def test_rerun_never_rewrites_a_narrowed_plan(db):
    await db.upsert_plan("free", label="Free", included=["vehicles"], quotas={"max_users": 3},
                         updated_by="owner:1")
    await migrate_plans(db._db)
    row = await db.get_plan("free")
    assert row["included"] == ["vehicles"]
    assert row["quotas"] == {"max_users": 3}
    assert row["updated_by"] == "owner:1"
    # and the other tiers are still there, untouched
    assert (await db.get_plan("pro"))["included"] == ["*"]


@pytest.mark.asyncio
async def test_upsert_round_trips_and_get_says_none_for_a_plan_that_is_not(db):
    out = await db.upsert_plan("gold", label="Gold", included=["*"], quotas={"max_companies": 9},
                               updated_by="owner:1")
    assert out["tier"] == "gold" and out["included"] == ["*"] and out["quotas"] == {"max_companies": 9}
    out2 = await db.upsert_plan("gold", label="Gold+", included=["vehicles", "maintenance"])
    assert out2["label"] == "Gold+" and out2["included"] == ["vehicles", "maintenance"]
    assert out2["quotas"] == {}                       # a write replaces the row's quotas
    assert await db.get_plan("nope") is None


@pytest.mark.asyncio
async def test_a_corrupt_row_reads_as_empty_not_as_a_crash(db):
    await db._db.execute(
        "UPDATE plans SET included = ?, quotas = ? WHERE tier = ?", ("not json", "[]", "free"))
    await db._db.commit()
    row = await db.get_plan("free")
    assert row["included"] == [] and row["quotas"] == {}
