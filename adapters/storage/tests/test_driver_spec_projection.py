"""``project_provider_driver_spec`` — filling a driver an admin linked.

Every test here is about a REFUSAL as much as a write, because the
thing this method must never do is decide who somebody is. It matches
on a link a human made, it fills blanks, and it has no INSERT in it.
"""

from __future__ import annotations

import pytest

from adapters.storage import Role


def _row(pdid: str, *, name: str = "", phone: str = "",
         cdl: str = "", state: str = "") -> dict:
    """One row in the ``get_driver_spec`` shape."""
    return {
        "provider_driver_id": pdid,
        "display_name": name,
        "phone": phone,
        "cdl_number": cdl,
        "cdl_state": state,
        "company_code": "",
    }


@pytest.mark.asyncio
async def test_fills_a_linked_driver_and_never_creates_one(db):
    """The linked member gets the blanks filled; the unlinked row is
    counted and nothing else — no new user appears for it."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2001, acct.id, role=Role.DRIVER,
                             display_name="John Smith")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "17")

    before = len(await db.list_drivers(acct.id))
    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("17", name="John Smith", phone="+15551111",
             cdl="D123456", state="CA"),
        _row("99", name="Nobody Here", phone="+15552222", cdl="X9", state="TX"),
    ])

    assert counts["linked"] == 1
    assert counts["written"] == 1
    assert counts["filled_cdl"] == 1
    assert counts["skipped_unlinked"] == 1
    assert len(await db.list_drivers(acct.id)) == before, (
        "a driver feed created a person"
    )
    p = await db.get_driver_profile(u.id)
    assert p.phone == "+15551111"
    assert p.cdl_number == "D123456"
    assert p.cdl_state == "CA"


@pytest.mark.asyncio
async def test_an_operator_typed_licence_is_never_touched(db):
    """CDL is fill-only. A number HR typed stays, and the provider's
    STATE does not land beside it — a state from one licence next to a
    number from another describes a licence that does not exist."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2002, acct.id, role=Role.DRIVER,
                             display_name="Jane Doe")
    await db.update_driver_profile(u.id, cdl_number="HR-999", cdl_state="NY")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "18")

    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("18", cdl="ELD-111", state="TX"),
    ])

    assert counts["filled_cdl"] == 0
    p = await db.get_driver_profile(u.id)
    assert p.cdl_number == "HR-999"
    assert p.cdl_state == "NY"


@pytest.mark.asyncio
async def test_a_licence_an_operator_deleted_stays_deleted(db):
    """Empty is not the same as never filled.

    Clearing a field through ``update_driver_profile`` PINS it manual,
    blank value and all. Reading only "is the column empty" makes an
    operator's deletion look identical to a gap, and the next tick puts
    the number back — a human's correction silently undone on a DOT
    compliance field.
    """
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2012, acct.id, role=Role.DRIVER,
                             display_name="Cleared CDL")
    await db.update_driver_profile(u.id, cdl_number="WRONG-1")
    await db.update_driver_profile(u.id, cdl_number="")     # deliberate
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "32")

    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("32", cdl="ELD-32", state="CA"),
    ])

    assert counts["filled_cdl"] == 0
    p = await db.get_driver_profile(u.id)
    assert not (p.cdl_number or ""), "a deleted licence came back"


@pytest.mark.asyncio
async def test_the_licence_state_travels_with_the_number(db):
    """Filled together or not at all — the pair is one statement."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2003, acct.id, role=Role.DRIVER,
                             display_name="Sam Ray")
    await db.update_driver_profile(u.id, cdl_state="NY")   # state only
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "19")

    await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("19", cdl="ELD-222", state="TX"),
    ])

    p = await db.get_driver_profile(u.id)
    assert p.cdl_number == "ELD-222"
    assert p.cdl_state == "TX", (
        "the number came from the provider, so its state must too"
    )


@pytest.mark.asyncio
async def test_an_operator_edit_still_outranks_the_provider(db):
    """``manual`` wins over every source, which is the rule the whole
    hub is built on — an ELD roster does not get to rename somebody."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2004, acct.id, role=Role.DRIVER,
                             display_name="Robert Klein")
    await db.update_driver_profile(u.id, phone="+15550000")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "20")

    await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("20", name="BOB K", phone="+15559999"),
    ])

    p = await db.get_driver_profile(u.id)
    assert p.display_name == "Robert Klein"
    assert p.phone == "+15550000"


@pytest.mark.asyncio
async def test_a_terminated_member_is_left_alone(db):
    """The providers here cannot say whether somebody still works for
    the carrier, so the roster read decides it — and it excludes
    terminated members. A live feed must not keep filling them."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2005, acct.id, role=Role.DRIVER,
                             display_name="Gone Away")
    await db.update_driver_profile(u.id, termination_date="2026-01-31")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "21")

    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("21", phone="+15557777", cdl="D77", state="CA"),
    ])

    assert counts["written"] == 0
    assert counts["skipped_unlinked"] == 1
    p = await db.get_driver_profile(u.id)
    assert not (p.phone or "")
    assert not (p.cdl_number or "")


@pytest.mark.asyncio
async def test_nothing_happens_without_a_link(db):
    """No link, no write — there is no fallback key by design."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2006, acct.id, role=Role.DRIVER,
                             display_name="Unlinked Person")

    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("22", name="Unlinked Person", phone="+15558888",
             cdl="D88", state="CA"),
    ])

    assert counts == {
        "linked": 0, "written": 0, "filled_cdl": 0,
        "skipped_unlinked": 1, "conflicts": 0,
    }
    p = await db.get_driver_profile(u.id)
    assert not (p.phone or "")


@pytest.mark.asyncio
async def test_a_matching_name_is_still_not_a_link(db):
    """The one above stops at the account's first gate — no links at
    all, so the loop never runs. This one puts a link on the account so
    execution reaches the per-row match, and hands the unlinked row the
    most tempting key there is: a display name that matches a member
    exactly. It must still be skipped.

    Every automatic key was measured on the live account and would have
    linked nobody (roster: 8 drivers, 0 licences, 5 emails; provider:
    26 drivers, 26 licences, 12 emails; email overlap zero). Names are
    worse than useless — two "John Smith"s are ordinary in this
    industry, and the wrong guess writes one person's licence number
    onto the other's record.
    """
    acct = await db.create_account("ELD Fleet")
    linked = await db.create_user(2010, acct.id, role=Role.DRIVER,
                                  display_name="Linked Person")
    stranger = await db.create_user(2011, acct.id, role=Role.DRIVER,
                                    display_name="Same Name")
    await db.link_provider_driver(acct.id, "orient_eld", linked.id, "30")

    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("30", phone="+15550001"),
        _row("31", name="Same Name", phone="+15550002",
             cdl="D31", state="CA"),
    ])

    assert counts["linked"] == 1
    assert counts["skipped_unlinked"] == 1
    p = await db.get_driver_profile(stranger.id)
    assert not (p.phone or ""), "a name match wrote to an unlinked member"
    assert not (p.cdl_number or "")


@pytest.mark.asyncio
async def test_the_licence_is_stored_encrypted(db):
    """``cdl_number`` is PII: the profile read decrypts it, so the raw
    column must not equal the plaintext."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2007, acct.id, role=Role.DRIVER,
                             display_name="Crypt Test")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "23")

    await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("23", cdl="PLAINTEXT-1", state="CA"),
    ])

    p = await db.get_driver_profile(u.id)
    assert p.cdl_number == "PLAINTEXT-1"
    cur = await db._db.execute(
        "SELECT cdl_number FROM users WHERE id = ?", (u.id,),
    )
    stored = str((await cur.fetchone())[0] or "")
    assert stored, "nothing was written"
    from adapters.storage.drivers import _maybe_encrypt
    if _maybe_encrypt("probe") != "probe":     # encryption is configured
        assert stored != "PLAINTEXT-1"


@pytest.mark.asyncio
async def test_provenance_names_the_provider_per_field(db):
    """Which source gave which field is answered on the ROW — that is
    what lets the feed log counts instead of copying driver PII into
    every log sink."""
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2008, acct.id, role=Role.DRIVER,
                             display_name="Prov Test")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "24")

    await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("24", phone="+15556666", cdl="D66", state="CA"),
    ])

    cur = await db._db.execute(
        "SELECT driver_field_provenance FROM users WHERE id = ?", (u.id,),
    )
    from adapters.storage.drivers import _parse_driver_provenance
    prov = _parse_driver_provenance((await cur.fetchone())[0])
    assert prov["phone"] == "orient_eld"
    assert prov["cdl_number"] == "orient_eld"
    assert prov["cdl_state"] == "orient_eld"


@pytest.mark.asyncio
async def test_a_pii_field_never_becomes_a_conflict_row(db):
    """``data_conflicts`` stores values in PLAINTEXT. A phone that two
    sources disagree about may be merged, but it may not be RECORDED.

    The licence in the row below is load-bearing. Without it the merge
    changes nothing, and a row that changes nothing is skipped by the
    guard ABOVE the conflict filter — so the test would pass whether
    the filter existed, was inverted, or was deleted. It did exactly
    that in its first version. The licence makes a real write happen on
    the same row, which is what carries execution down to the filter.
    """
    acct = await db.create_account("ELD Fleet")
    u = await db.create_user(2009, acct.id, role=Role.DRIVER,
                             display_name="Conflict Test")
    await db.link_provider_driver(acct.id, "orient_eld", u.id, "25")
    # A phone owned by another integration, not by the operator.
    await db.project_datatruck_drivers(acct.id, [{
        "external_id": "dt-1", "first_name": "", "last_name": "",
        "display_name": "", "phone": "", "email": "", "status": "active",
        "payload": {},
    }])
    await db.update_driver_profile(u.id, phone="+15551234")
    cur = await db._db.execute(
        "UPDATE users SET driver_field_provenance = ? WHERE id = ?",
        ('{"phone": "datatruck"}', u.id),
    )
    await db._db.commit()

    counts = await db.project_provider_driver_spec(acct.id, "orient_eld", [
        _row("25", phone="+15559999", cdl="D25", state="CA"),
    ])

    assert counts["written"] == 1, (
        "nothing was written, so the conflict filter was never reached "
        "and this test proves nothing"
    )
    assert counts["conflicts"] == 0, "a PII value reached the conflict store"
    cur = await db._db.execute(
        "SELECT COUNT(*) FROM data_conflicts "
        "WHERE account_id = ? AND field = 'phone'",
        (acct.id,),
    )
    assert int((await cur.fetchone())[0] or 0) == 0
