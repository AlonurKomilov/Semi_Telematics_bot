"""Integration tests for accounts.alert_routing_mode.

The column powers the resolver's mode-branch.  Defaults must be
``'single_group'`` so every existing account continues to route to its
legacy forum topic without any operator action.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_new_account_defaults_to_single_group(db):
    acct = await db.create_account("Default Mode Co")
    fetched = await db.get_account(acct.id)
    assert fetched is not None
    assert fetched.alert_routing_mode == "single_group"


@pytest.mark.asyncio
async def test_set_alert_routing_mode_to_per_persona(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]

    ok = await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    assert ok is True

    fetched = await db.get_account(acct.id)
    assert fetched.alert_routing_mode == "per_persona_groups"


@pytest.mark.asyncio
async def test_set_alert_routing_mode_back_to_single_group(seeded_db):
    """Operator can revert without losing the persona-group config —
    rows in account_persona_groups stay; the resolver just ignores
    them while the mode is single_group."""
    db = seeded_db["db"]
    acct = seeded_db["account"]

    await db.set_alert_routing_mode(acct.id, "per_persona_groups")
    await db.upsert_persona_group(
        account_id=acct.id, persona="dispatcher",
        chat_id=-1, chat_title="d",
    )

    await db.set_alert_routing_mode(acct.id, "single_group")
    fetched = await db.get_account(acct.id)
    assert fetched.alert_routing_mode == "single_group"

    # Persona group survived the mode flip.
    assert await db.get_persona_group(acct.id, "dispatcher") is not None


@pytest.mark.asyncio
async def test_invalid_mode_raises_value_error(seeded_db):
    db = seeded_db["db"]
    acct = seeded_db["account"]
    with pytest.raises(ValueError):
        await db.set_alert_routing_mode(acct.id, "owner_admin_only")


@pytest.mark.asyncio
async def test_update_account_via_kwargs_also_accepts_mode(seeded_db):
    """The setter helper is preferred, but the generic update path
    also exposes the column (the admin update form may set it
    alongside other fields)."""
    db = seeded_db["db"]
    acct = seeded_db["account"]

    ok = await db.update_account(acct.id, alert_routing_mode="per_persona_groups")
    assert ok is True
    fetched = await db.get_account(acct.id)
    assert fetched.alert_routing_mode == "per_persona_groups"
