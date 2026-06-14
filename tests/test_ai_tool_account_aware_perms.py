"""The AI tool gate honors per-account permission overrides (P3 / Stage 1).

The runtime gate ``_check_tool_permission`` used to resolve from STATIC role
defaults, so an account that revoked a flag through the Role Permissions
matrix (or disabled a module) was silently ignored by the AI assistant — a
within-account authorization bypass.  The gate now resolves through
``get_account_permissions`` (the same source of truth the API / dashboard /
bot use), so per-account overrides are enforced in the AI path too, and stay
isolated to the account that set them.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage import Database
from capabilities.ai.intelligence import _check_tool_permission
import capabilities.permissions.roles as perms_mod


@pytest.fixture(autouse=True)
def _clear_perms_cache():
    # The permissions cache is a module global; clear around each test so a
    # cached entry from a sibling test can't mask the override under test.
    perms_mod.invalidate_permissions_cache()
    yield
    perms_mod.invalidate_permissions_cache()


@pytest.mark.asyncio
async def test_fleet_default_allows_account_wide_tool(seeded_db, monkeypatch):
    """Baseline: with no override, fleet's role default (can_maintenance_all)
    lets the gate allow a maintenance tool."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    result = await _check_tool_permission(
        "get_maintenance_summary", {}, "fleet", {}, account_id=account.id,
    )
    assert result is None  # allowed


@pytest.mark.asyncio
async def test_per_account_override_blocks_ai_tool(seeded_db, monkeypatch):
    """Revoking can_maintenance_all for fleet on this account blocks the AI
    maintenance tool — the override is honored, not the static default."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    await db.set_role_permissions(
        account.id, "fleet", {"can_maintenance_all": False},
    )
    perms_mod.invalidate_permissions_cache()

    result = await _check_tool_permission(
        "get_maintenance_summary", {}, "fleet", {}, account_id=account.id,
    )
    assert result is not None
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_override_isolated_to_its_account(seeded_db, monkeypatch):
    """An override on one account must NOT bleed into another — a second
    account's fleet still gets the default allow."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    other = await db.create_account("Second Fleet Co")
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    await db.set_role_permissions(
        account.id, "fleet", {"can_maintenance_all": False},
    )
    perms_mod.invalidate_permissions_cache()

    blocked = await _check_tool_permission(
        "get_maintenance_summary", {}, "fleet", {}, account_id=account.id,
    )
    allowed = await _check_tool_permission(
        "get_maintenance_summary", {}, "fleet", {}, account_id=other.id,
    )
    assert blocked is not None   # restricted account: blocked
    assert allowed is None       # other account: still allowed
