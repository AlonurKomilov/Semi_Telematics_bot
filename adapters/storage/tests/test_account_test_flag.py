"""accounts.is_test — operator Test/Real classification.

A classification, deliberately NOT a lifecycle state: flipping it must
never touch is_active (deactivating a test account would defeat its
purpose — the June-2026 signup-flow artifacts exist to be logged into).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_default_is_real_and_flag_round_trips(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]

    fresh = await db.get_account(account.id)
    assert fresh.is_test is False          # default: real

    assert await db.update_account(account.id, is_test=1) is True
    marked = await db.get_account(account.id)
    assert marked.is_test is True
    assert marked.is_active is True        # classification ≠ lifecycle

    assert await db.update_account(account.id, is_test=0) is True
    back = await db.get_account(account.id)
    assert back.is_test is False


@pytest.mark.asyncio
async def test_list_accounts_carries_the_flag(seeded_db):
    db = seeded_db["db"]
    account = seeded_db["account"]
    await db.update_account(account.id, is_test=1)
    rows = await db.list_accounts(active_only=False)
    by_id = {a.id: a for a in rows}
    assert by_id[account.id].is_test is True
