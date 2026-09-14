"""The ELD mirror stores what the provider said, including about
drivers we have not linked yet.

``driver_hos_status`` — the table this replaces — is keyed on our own
``user_id``, so a driver nobody had linked to the provider simply had
no row.  The provider knows about that driver and reports their clocks;
losing them at the door makes an account's HOS answer quietly
incomplete, which on a compliance surface is the same shape of bug as
answering zero.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from capabilities.permissions.roles import Role


@pytest_asyncio.fixture
async def sdb(pg_db):
    yield pg_db


def _row(pdid, **kw):
    base = {
        "provider_driver_id": pdid,
        "duty_status": "driving",
        "drive_seconds_today": 3600,
        "on_duty_seconds_today": 7200,
        "cycle_seconds_remaining": 180000,
        "shift_seconds_remaining": 25200,
        "last_status_change": "2026-09-14T08:00:00+00:00",
        "driver_name": f"Provider Name {pdid}",
        "source_ts": "2026-09-14T09:00:00+00:00",
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_an_unlinked_driver_is_still_stored(sdb):
    acct = await sdb.create_account("ELD Co")
    n = await sdb.upsert_driver_hos(acct.id, "samsara", [_row("p1")])
    assert n == 1

    rows = await sdb.get_driver_hos_live(acct.id)
    assert len(rows) == 1
    assert rows[0]["user_id"] is None
    assert rows[0]["linked"] is False
    # With no roster name, the provider's spelling is the fallback —
    # a diagnostic, not a preference.
    assert rows[0]["display_name"] == "Provider Name p1"


@pytest.mark.asyncio
async def test_a_link_enriches_the_row_without_being_its_key(sdb):
    acct = await sdb.create_account("ELD Co 2")
    user = await sdb.create_user(
        account_id=acct.id, telegram_id=990001, role=Role.DRIVER,
        display_name="Jane Ruiz",
    )
    await sdb.upsert_driver_hos(
        acct.id, "samsara", [_row("p1")], links={"p1": user.id},
    )

    rows = await sdb.get_driver_hos_live(acct.id)
    assert rows[0]["user_id"] == user.id
    assert rows[0]["linked"] is True
    # OUR roster name wins once we know it.
    assert rows[0]["display_name"] == "Jane Ruiz"


@pytest.mark.asyncio
async def test_an_unreported_clock_stays_null(sdb):
    """NULL means the provider did not report that clock.  Zero means
    the driver is out of hours.  They are opposite answers, and a
    DEFAULT 0 column would turn one into the other."""
    acct = await sdb.create_account("ELD Co 3")
    await sdb.upsert_driver_hos(acct.id, "samsara", [_row(
        "p1", cycle_seconds_remaining=None, shift_seconds_remaining=0,
    )])

    row = (await sdb.get_driver_hos_live(acct.id))[0]
    assert row["cycle_seconds_remaining"] is None
    assert row["shift_seconds_remaining"] == 0


@pytest.mark.asyncio
async def test_a_second_reading_replaces_the_first(sdb):
    acct = await sdb.create_account("ELD Co 4")
    await sdb.upsert_driver_hos(acct.id, "samsara", [_row("p1")])
    await sdb.upsert_driver_hos(acct.id, "samsara", [_row(
        "p1", duty_status="off_duty", source_ts="2026-09-14T10:00:00+00:00",
    )])

    rows = await sdb.get_driver_hos_live(acct.id)
    assert len(rows) == 1
    assert rows[0]["duty_status"] == "off_duty"
    assert rows[0]["source_ts"] == "2026-09-14T10:00:00+00:00"


@pytest.mark.asyncio
async def test_two_providers_do_not_overwrite_each_other(sdb):
    """A fleet mid-migration can run two ELDs.  The key carries the
    provider, so one vendor's driver ids cannot land on another's."""
    acct = await sdb.create_account("ELD Co 5")
    await sdb.upsert_driver_hos(acct.id, "samsara", [_row("shared-id")])
    await sdb.upsert_driver_hos(
        acct.id, "motive", [_row("shared-id", duty_status="sleeper")])

    rows = await sdb.get_driver_hos_live(acct.id)
    assert {r["provider_id"] for r in rows} == {"samsara", "motive"}


@pytest.mark.asyncio
async def test_a_row_with_no_provider_id_is_skipped_not_invented(sdb):
    acct = await sdb.create_account("ELD Co 6")
    n = await sdb.upsert_driver_hos(
        acct.id, "samsara", [_row(""), _row("p1")])

    assert n == 1
    rows = await sdb.get_driver_hos_live(acct.id)
    assert [r["provider_driver_id"] for r in rows] == ["p1"]


@pytest.mark.asyncio
async def test_one_accounts_clocks_never_appear_under_another(sdb):
    """Driver duty status is FMCSA-regulated PII about a named person.
    The account filter is the wall."""
    a = await sdb.create_account("Carrier A")
    b = await sdb.create_account("Carrier B")
    await sdb.upsert_driver_hos(a.id, "samsara", [_row("a1")])
    await sdb.upsert_driver_hos(b.id, "samsara", [_row("b1")])

    assert [r["provider_driver_id"]
            for r in await sdb.get_driver_hos_live(a.id)] == ["a1"]
    assert [r["provider_driver_id"]
            for r in await sdb.get_driver_hos_live(b.id)] == ["b1"]


@pytest.mark.asyncio
async def test_the_count_tells_never_ingested_from_all_clear(sdb):
    """Zero rows means nothing has ever been ingested, which is NOT a
    statement that every driver has hours remaining.  The caller needs
    to be able to ask."""
    acct = await sdb.create_account("ELD Co 7")
    assert await sdb.count_driver_hos_live(acct.id) == 0
    await sdb.upsert_driver_hos(acct.id, "samsara", [_row("p1")])
    assert await sdb.count_driver_hos_live(acct.id) == 1


@pytest.mark.asyncio
async def test_asking_for_one_driver_returns_only_theirs(sdb):
    acct = await sdb.create_account("ELD Co 8")
    u1 = await sdb.create_user(
        account_id=acct.id, telegram_id=990002, role=Role.DRIVER,
        display_name="One")
    u2 = await sdb.create_user(
        account_id=acct.id, telegram_id=990003, role=Role.DRIVER,
        display_name="Two")
    await sdb.upsert_driver_hos(
        acct.id, "samsara", [_row("p1"), _row("p2")],
        links={"p1": u1.id, "p2": u2.id},
    )

    rows = await sdb.get_driver_hos_live(acct.id, user_id=u2.id)
    assert [r["display_name"] for r in rows] == ["Two"]


@pytest.mark.asyncio
async def test_nothing_to_write_is_not_an_error(sdb):
    acct = await sdb.create_account("ELD Co 9")
    assert await sdb.upsert_driver_hos(acct.id, "samsara", []) == 0
