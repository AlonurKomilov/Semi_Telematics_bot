"""One table for "which provider driver is which member" — any provider.

There were already TWO vendor-named columns holding this:
``users.samsara_driver_id`` and ``users.datatruck_driver_id``, each
with its own write method, its own picker and its own uniqueness
check. A third ELD would have made three, which is the rename this
project has done twice, and ``features/eld/ingest`` was carrying the
confession in a comment.

Three properties matter more than the schema:

  THE LEGACY COLUMNS STILL COUNT. They hold live links and four
  features read them. Every read merges them UNDER the new table, so
  this lands without a data migration and without a flag day.

  A LINK IS NEVER SILENTLY MOVED. Binding a provider driver already
  bound to somebody else would move that person's hours of service onto
  a different member — on a compliance surface, with nothing said.

  ONE MEMBER HOLDS ONE IDENTITY PER PROVIDER. Two ids for one person on
  one device makes every reader pick whichever row sorts first.
"""

from __future__ import annotations

import pytest


async def _driver(db, account_id, telegram_id, name):
    """A second member to contend over a link with."""
    from adapters.storage.models import Role

    u = await db.create_user(
        telegram_id=telegram_id, account_id=account_id,
        role=Role.DRIVER, display_name=name,
    )
    return u.id


@pytest.mark.asyncio
async def test_a_link_is_stored_and_read_back(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id

    await db.link_provider_driver(acct, "orient_eld", uid, "19192")

    assert await db.driver_links_for(acct, "orient_eld") == {"19192": uid}


@pytest.mark.asyncio
async def test_a_provider_nobody_has_a_column_for_works(seeded_db):
    """The whole point: a link for a provider that has no vendor-named
    column anywhere, which is every provider from the third on."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id

    await db.link_provider_driver(acct, "brand_new_eld", uid, "abc-1")

    assert await db.driver_links_for(acct, "brand_new_eld") == {"abc-1": uid}
    # And it did not leak into another provider's answer.
    assert await db.driver_links_for(acct, "orient_eld") == {}


@pytest.mark.asyncio
async def test_the_legacy_column_is_still_read(seeded_db):
    """Four features read these today. A replacement that stopped
    seeing them would unlink every driver on every existing account."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id
    await db.link_samsara_driver(acct, uid, "sam-77")

    assert await db.driver_links_for(acct, "samsara") == {"sam-77": uid}


@pytest.mark.asyncio
async def test_the_new_table_wins_over_the_legacy_column(seeded_db):
    """Both can hold an answer for one provider. The table is the newer
    statement and the one an admin made most recently."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id
    await db.link_samsara_driver(acct, uid, "old-id")

    await db.link_provider_driver(acct, "samsara", uid, "new-id")

    links = await db.driver_links_for(acct, "samsara")
    assert links["new-id"] == uid


@pytest.mark.asyncio
async def test_taking_another_members_driver_is_refused(seeded_db):
    """Silently moving it would move that person's duty clocks onto a
    different member, on a compliance surface."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    a = seeded_db["owner"].id
    b = await _driver(db, acct, 900900901, "Second Driver")
    await db.link_provider_driver(acct, "orient_eld", a, "19192")

    with pytest.raises(ValueError, match="already linked"):
        await db.link_provider_driver(acct, "orient_eld", b, "19192")

    assert await db.driver_links_for(acct, "orient_eld") == {"19192": a}


@pytest.mark.asyncio
async def test_relinking_the_same_member_replaces_rather_than_adds(seeded_db):
    """One person cannot be two drivers on one device."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id

    await db.link_provider_driver(acct, "orient_eld", uid, "first")
    await db.link_provider_driver(acct, "orient_eld", uid, "second")

    assert await db.driver_links_for(acct, "orient_eld") == {"second": uid}


@pytest.mark.asyncio
async def test_a_member_may_hold_one_identity_per_provider(seeded_db):
    """Two providers, one person — that is normal, not a conflict."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id

    await db.link_provider_driver(acct, "orient_eld", uid, "o-1")
    await db.link_provider_driver(acct, "brand_new_eld", uid, "b-1")

    assert await db.driver_links_for(acct, "orient_eld") == {"o-1": uid}
    assert await db.driver_links_for(acct, "brand_new_eld") == {"b-1": uid}


@pytest.mark.asyncio
async def test_unlinking_with_a_blank_removes_it(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id
    await db.link_provider_driver(acct, "orient_eld", uid, "19192")

    await db.link_provider_driver(acct, "orient_eld", uid, "")

    assert await db.driver_links_for(acct, "orient_eld") == {}


@pytest.mark.asyncio
async def test_relinking_the_same_pair_is_not_an_error(seeded_db):
    """An admin clicking Save twice is not a conflict with themselves."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id

    await db.link_provider_driver(acct, "orient_eld", uid, "19192")
    await db.link_provider_driver(acct, "orient_eld", uid, "19192")

    assert await db.driver_links_for(acct, "orient_eld") == {"19192": uid}


@pytest.mark.asyncio
async def test_how_the_link_was_made_is_recorded(seeded_db):
    """"An admin chose this" and "a CDL matched" carry different weight
    when one turns out to be wrong, and an audit of driver PII should
    be able to tell them apart."""
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id

    await db.link_provider_driver(
        acct, "orient_eld", uid, "19192", method="cdl", linked_by=7)

    cur = await db._db.execute(
        "SELECT link_method, linked_by FROM driver_provider_links "
        "WHERE account_id = ? AND provider_id = ?", (acct, "orient_eld"))
    row = (await cur.fetchall())[0]
    assert row[0] == "cdl"
    assert row[1] == 7


@pytest.mark.asyncio
async def test_one_accounts_links_are_invisible_to_another(seeded_db):
    db, acct = seeded_db["db"], seeded_db["account"].id
    uid = seeded_db["owner"].id
    await db.link_provider_driver(acct, "orient_eld", uid, "19192")

    assert await db.driver_links_for(acct + 999, "orient_eld") == {}
