"""Integration tests for AccountPersonaGroupsMixin against Postgres.

Uses the session-scoped ``pg_db`` fixture for a real Postgres backend
so the SQL stays portable through the pg_adapter translator (the same
UNIQUE-constraint upsert behavior is what the resolver relies on).
"""

from __future__ import annotations

import pytest

from adapters.storage.models import PersonaGroup


@pytest.mark.asyncio
async def test_upsert_persona_group_creates_active_row(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]

    row = await db.upsert_persona_group(
        account_id=acct.id,
        persona="dispatcher",
        chat_id=-100123,
        chat_title="Dispatch Group",
    )
    assert isinstance(row, PersonaGroup)
    assert row.account_id == acct.id
    assert row.persona == "dispatcher"
    assert row.chat_id == -100123
    assert row.chat_title == "Dispatch Group"
    assert row.is_active is True


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_persona_chat(seeded_db):
    """Upsert with same (account, persona) updates chat_id — used when
    an admin moves a persona to a different group."""
    db = seeded_db["db"]
    acct = seeded_db["account"]

    await db.upsert_persona_group(
        account_id=acct.id, persona="safety",
        chat_id=-100200, chat_title="Safety v1",
    )
    row = await db.upsert_persona_group(
        account_id=acct.id, persona="safety",
        chat_id=-100201, chat_title="Safety v2",
    )
    assert row.chat_id == -100201
    assert row.chat_title == "Safety v2"

    # Lookup returns the updated row.
    fetched = await db.get_persona_group(acct.id, "safety")
    assert fetched is not None
    assert fetched.chat_id == -100201


@pytest.mark.asyncio
async def test_upsert_reactivates_disabled_row(seeded_db):
    """Operator deactivated → admin re-registers same persona → should
    flip is_active back to True without losing the existing chat_id."""
    db = seeded_db["db"]
    acct = seeded_db["account"]

    await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-100300, chat_title="Fleet",
    )
    await db.set_persona_group_active(acct.id, "fleet", active=False)
    assert await db.get_persona_group(acct.id, "fleet") is None

    # Re-upsert reactivates.
    row = await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-100300, chat_title="Fleet",
    )
    assert row.is_active is True
    assert (await db.get_persona_group(acct.id, "fleet")) is not None


@pytest.mark.asyncio
async def test_get_persona_group_returns_none_for_disabled(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]

    await db.upsert_persona_group(
        account_id=acct.id, persona="hr",
        chat_id=-100400, chat_title="HR",
    )
    await db.set_persona_group_active(acct.id, "hr", active=False)
    assert await db.get_persona_group(acct.id, "hr") is None


@pytest.mark.asyncio
async def test_list_persona_groups_includes_inactive(seeded_db):
    """Admin UI needs to see disabled rows so it can re-enable them."""
    db = seeded_db["db"]
    acct = seeded_db["account"]

    await db.upsert_persona_group(
        account_id=acct.id, persona="dispatcher",
        chat_id=-1, chat_title="d",
    )
    await db.upsert_persona_group(
        account_id=acct.id, persona="fleet",
        chat_id=-2, chat_title="f",
    )
    await db.set_persona_group_active(acct.id, "fleet", active=False)

    rows = await db.list_persona_groups(acct.id)
    assert len(rows) == 2
    by_persona = {r.persona: r for r in rows}
    assert by_persona["dispatcher"].is_active is True
    assert by_persona["fleet"].is_active is False


@pytest.mark.asyncio
async def test_delete_persona_group_is_permanent(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]

    await db.upsert_persona_group(
        account_id=acct.id, persona="hr",
        chat_id=-99, chat_title="HR",
    )
    await db.delete_persona_group(acct.id, "hr")
    rows = await db.list_persona_groups(acct.id)
    assert all(r.persona != "hr" for r in rows)


@pytest.mark.asyncio
async def test_persona_groups_are_per_account(seeded_db, db):
    """A second account with the same persona key has its own row."""
    acct1 = seeded_db["account"]
    acct2 = await db.create_account("Other Co")

    await db.upsert_persona_group(
        account_id=acct1.id, persona="dispatcher",
        chat_id=-1, chat_title="acct1 dispatch",
    )
    await db.upsert_persona_group(
        account_id=acct2.id, persona="dispatcher",
        chat_id=-2, chat_title="acct2 dispatch",
    )

    r1 = await db.get_persona_group(acct1.id, "dispatcher")
    r2 = await db.get_persona_group(acct2.id, "dispatcher")
    assert r1 is not None and r1.chat_id == -1
    assert r2 is not None and r2.chat_id == -2
