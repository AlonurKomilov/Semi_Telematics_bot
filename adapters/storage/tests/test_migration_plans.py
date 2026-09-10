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
