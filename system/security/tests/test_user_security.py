"""Watching one person, without watching the twenty-two beside them.

Security standing lived only on the account until 2026-09-13. An account
is often fine while one person inside it is not — a dispatcher probing
admin endpoints, stolen credentials in use from somewhere new, an
ex-employee nobody deactivated — and the only way to observe them was to
mark their ACCOUNT monitored, which records every request from everyone
in it. At the largest customer that is twenty-three people to watch one:
noise, and twenty-two innocents treated as suspects.
"""
import pytest

from adapters.storage.models import ACCOUNT_SECURITY
from system.security import recorder


@pytest.fixture(autouse=True)
def _clean_cache():
    recorder.forget_security()
    yield
    recorder.forget_security()


def test_both_subjects_speak_one_vocabulary():
    """A second word list for the same three states is how a console and
    a column start disagreeing."""
    from adapters.storage.models import Account, User
    import dataclasses
    af = {f.name: f.default for f in dataclasses.fields(Account)}
    uf = {f.name: f.default for f in dataclasses.fields(User)}
    assert af["security"] == uf["security"] == "normal"
    assert "monitored" in ACCOUNT_SECURITY


def test_the_strongest_standing_decides():
    """A request is kept when EITHER subject is watched, and the ledger
    records the reason that applied."""
    assert recorder.strongest(None, None) is None
    assert recorder.strongest("normal", None) == "normal"
    assert recorder.strongest("normal", "monitored") == "monitored"
    assert recorder.strongest("monitored", "normal") == "monitored"
    assert recorder.strongest("monitored", "quarantined") == "quarantined"


@pytest.mark.asyncio
async def test_a_user_can_be_watched_inside_a_clean_account(seeded_db):
    db, acct, owner = seeded_db["db"], seeded_db["account"], seeded_db["owner"]
    await db.update_user(owner.id, security="monitored")

    fresh_user = await db.get_user_by_id(owner.id)
    fresh_acct = await db.get_account(acct.id)
    assert fresh_user.security == "monitored"
    assert fresh_acct.security == "normal", "watching a person must not mark their employer"
    assert fresh_acct.kind == "real", "...nor change what the account is"


@pytest.mark.asyncio
async def test_the_user_column_refuses_a_word_it_does_not_know(seeded_db):
    db, owner = seeded_db["db"], seeded_db["owner"]
    with pytest.raises(ValueError, match="security"):
        await db.update_user(owner.id, security="watched")
    assert (await db.get_user_by_id(owner.id)).security == "normal"


@pytest.mark.asyncio
async def test_a_row_is_kept_for_a_watched_person_in_a_clean_account(seeded_db, monkeypatch):
    """The point of the whole column, in one assertion."""
    db, acct, owner = seeded_db["db"], seeded_db["account"], seeded_db["owner"]
    await db.update_user(owner.id, security="monitored")
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    recorder.forget_security()

    kept = await recorder.record_request(
        method="GET", path="/api/vehicles", status=200,
        account_id=acct.id, user_id=owner.id, role="owner",
    )
    assert kept is True

    rows = await db.list_security_requests(account_id=acct.id)
    assert rows and rows[0]["user_id"] == owner.id
    assert rows[0]["security"] == "monitored", "the row must say why it was kept"


@pytest.mark.asyncio
async def test_their_colleague_is_not_recorded(seeded_db, monkeypatch):
    """Twenty-two innocents is the cost this column exists to avoid."""
    from adapters.storage import Role
    db, acct, owner = seeded_db["db"], seeded_db["account"], seeded_db["owner"]
    colleague = await db.create_user_with_email(
        email="colleague@premiertruckinggroup.com", password_hash="x",
        account_id=acct.id, role=Role.DISPATCHER, display_name="c")
    await db.update_user(owner.id, security="monitored")
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    recorder.forget_security()

    assert await recorder.record_request(
        method="GET", path="/api/loads", status=200,
        account_id=acct.id, user_id=colleague.id, role="dispatch") is False

    rows = await db.list_security_requests(account_id=acct.id)
    assert all(r["user_id"] != colleague.id for r in rows)


@pytest.mark.asyncio
async def test_a_refusal_is_still_kept_from_anyone(seeded_db, monkeypatch):
    """Per-user watching narrows what SUCCESS is recorded; it must not
    narrow the refusals, which are kept from everybody."""
    db, acct, owner = seeded_db["db"], seeded_db["account"], seeded_db["owner"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)
    recorder.forget_security()
    assert await recorder.record_request(
        method="GET", path="/api/system/accounts", status=403,
        account_id=acct.id, user_id=owner.id, role="owner") is True


def test_the_cache_cannot_confuse_an_account_with_a_user(monkeypatch):
    """Account 7 and user 7 both exist, always."""
    recorder._security_cache[("account", 7)] = ("monitored", 1e18)
    recorder._security_cache[("user", 7)] = ("normal", 1e18)
    recorder.forget_security(7, kind="user")
    assert ("account", 7) in recorder._security_cache
    assert ("user", 7) not in recorder._security_cache


def test_a_held_subject_is_watched_at_least_as_closely_as_a_observed_one():
    """`quarantined` is the stronger standing of the two.  It would be
    incoherent for it to record LESS than `monitored` — a subject we
    have decided to hold, going quieter in the ledger than one we are
    merely observing."""
    from system.security.recorder import should_record
    for standing in ("monitored", "quarantined"):
        assert should_record(200, standing), f"{standing} must record everything"
    assert not should_record(200, "normal")
    assert not should_record(200, None)
    # ...and a denial is kept from anyone, standing or not
    for standing in (None, "normal", "monitored", "quarantined"):
        assert should_record(403, standing)
