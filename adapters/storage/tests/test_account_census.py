"""The census behind the operator's health card.

The card used to show only the customer counts, so a signup burst was
invisible on it until someone opened the console — which is how 34 probe
accounts sat counted as customers for two days. It shows both axes now,
and these are the claims that keeps honest.
"""
import pytest

from adapters.storage.models import ACCOUNT_KINDS, ACCOUNT_SECURITY


@pytest.mark.asyncio
async def test_the_census_covers_every_value_in_both_vocabularies(seeded_db):
    """A key missing from the census is a line missing from the card."""
    c = await seeded_db["db"].account_census()
    assert set(c["accounts"]) == {"total", *ACCOUNT_KINDS}
    assert set(c["users"]) == {"total", *ACCOUNT_KINDS}
    assert set(c["security"]) == set(ACCOUNT_SECURITY)


@pytest.mark.asyncio
async def test_the_breakdowns_add_up_to_their_totals(seeded_db):
    db = seeded_db["db"]
    c = await db.account_census()
    assert sum(c["accounts"][k] for k in ACCOUNT_KINDS) == c["accounts"]["total"]
    assert sum(c["users"][k] for k in ACCOUNT_KINDS) == c["users"]["total"]
    # The security breakdown counts the same accounts a second way, so
    # it must reach the same total or one of the two passes is wrong.
    assert sum(c["security"].values()) == c["accounts"]["total"]


@pytest.mark.asyncio
async def test_the_two_axes_are_counted_independently(seeded_db):
    """The failure the card exists to make visible: watching a customer
    must not move them out of the `real` column."""
    db, acct = seeded_db["db"], seeded_db["account"]
    before = await db.account_census()

    await db.update_account(acct.id, security="monitored")
    after = await db.account_census()

    assert after["accounts"]["real"] == before["accounts"]["real"]
    assert after["users"]["real"] == before["users"]["real"]
    assert after["security"]["monitored"] == before["security"]["monitored"] + 1
    assert after["security"]["normal"] == before["security"]["normal"] - 1
    assert after["accounts"]["total"] == before["accounts"]["total"]


@pytest.mark.asyncio
async def test_moving_an_account_to_ours_moves_its_users_too(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    before = await db.account_census()
    assert before["users"]["real"] > 0

    await db.update_account(acct.id, kind="test")
    after = await db.account_census()

    assert after["accounts"]["real"] == before["accounts"]["real"] - 1
    assert after["accounts"]["test"] == before["accounts"]["test"] + 1
    assert after["users"]["real"] < before["users"]["real"]
    assert after["users"]["total"] == before["users"]["total"], "nobody left the platform"


@pytest.mark.asyncio
async def test_an_inactive_user_is_not_counted_as_someone_using_this(seeded_db):
    """`users` answers "how many people use this", so a deactivated
    account holder is not one of them — while their ACCOUNT still is."""
    db, acct, owner = seeded_db["db"], seeded_db["account"], seeded_db["owner"]
    before = await db.account_census()
    await db._db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (owner.id,))
    await db._db.commit()
    after = await db.account_census()
    assert after["users"]["real"] == before["users"]["real"] - 1
    assert after["accounts"]["real"] == before["accounts"]["real"]
