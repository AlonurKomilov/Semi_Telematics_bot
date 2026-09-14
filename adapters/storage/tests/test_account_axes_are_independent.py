"""What an account IS, and how it STANDS, are two facts.

They shared one column until 2026-09-13. Marking an account `monitored`
therefore erased whether it was a customer — and `kind = 'real'` is what
the bot card counts and what billing charges, so watching a paying
customer would have quietly removed them from both. Every account
watched so far was ours, so no bill was ever wrong; the first watched
customer would have been.

These tests are that claim, stated so it cannot come back.
"""
import pytest

from adapters.storage.models import ACCOUNT_KINDS, ACCOUNT_SECURITY


def test_neither_vocabulary_contains_the_other():
    # The old column held all four words at once, which is what made the
    # two questions collapse into one answer.
    assert set(ACCOUNT_KINDS).isdisjoint(ACCOUNT_SECURITY)
    assert "monitored" not in ACCOUNT_KINDS
    assert "real" not in ACCOUNT_SECURITY


def test_normal_is_not_a_clearance():
    # `normal` means nothing has been said. A word like "safe" would
    # claim someone had looked, and is one letter from the Safety role.
    assert ACCOUNT_SECURITY[0] == "normal"
    assert "safe" not in ACCOUNT_SECURITY


@pytest.mark.asyncio
async def test_watching_a_customer_leaves_them_a_customer(seeded_db):
    """The failure this split exists to prevent, in one assertion."""
    db, acct = seeded_db["db"], seeded_db["account"]

    before = await db.count_all_users(kinds=("real",))
    assert before > 0, "the fixture account must start as a counted customer"

    await db.update_account(acct.id, security="monitored")

    fresh = await db.get_account(acct.id)
    assert fresh.kind == "real", "watching must not change what an account IS"
    assert fresh.security == "monitored"
    # The two readers that decide money and the health card.
    assert await db.count_all_users(kinds=("real",)) == before
    assert any(a.id == acct.id and a.kind == "real" for a in await db.list_accounts())


@pytest.mark.asyncio
async def test_an_account_can_be_ours_and_watched_at_once(seeded_db):
    """The combination every account watched so far is actually in."""
    db, acct = seeded_db["db"], seeded_db["account"]
    await db.update_account(acct.id, kind="test", security="monitored")
    fresh = await db.get_account(acct.id)
    assert (fresh.kind, fresh.security) == ("test", "monitored")
    # ...and it leaves the customer count, because of the kind, not the watch.
    assert not any(a.id == acct.id and a.kind == "real" for a in await db.list_accounts())


@pytest.mark.asyncio
async def test_neither_column_will_accept_the_other_vocabulary(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"]
    with pytest.raises(ValueError, match="kind"):
        await db.update_account(acct.id, kind="monitored")
    with pytest.raises(ValueError, match="security"):
        await db.update_account(acct.id, security="real")
    # and the row is untouched by either refusal
    fresh = await db.get_account(acct.id)
    assert (fresh.kind, fresh.security) == ("real", "normal")


@pytest.mark.asyncio
async def test_the_legacy_is_test_flag_never_reaches_the_security_axis(seeded_db):
    """`is_test` predates both columns and knows only one of them."""
    db, acct = seeded_db["db"], seeded_db["account"]
    await db.update_account(acct.id, security="monitored")
    await db.update_account(acct.id, is_test=1)
    fresh = await db.get_account(acct.id)
    assert fresh.kind == "test"
    assert fresh.security == "monitored", "a legacy writer must not clear a watch"
