"""Stage 2: AI tool ADVERTISEMENT is account-aware + scope-aware.

The list of tools the model is *shown* (not just what it may execute) reflects
the per-account Permissions matrix, module masking, and the user's Vehicle
Access scope.  This is efficiency/UX — the runtime gate remains the security
boundary — so a revoked feature's tools simply stop being advertised, and a
scoped user isn't shown account-wide tools the gate would block anyway.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage import Database
import capabilities.permissions.roles as perms_mod
from capabilities.permissions.roles import ACCOUNT_WIDE_TOOLS
from capabilities.ai.tools.registry import (
    filter_tools_for_role,
    get_anthropic_tools,
    invalidate_tool_cache,
)


def _names(defs):
    return {d["name"] for d in defs}


@pytest.fixture(autouse=True)
def _clear_caches():
    perms_mod.invalidate_permissions_cache()
    invalidate_tool_cache()
    yield
    perms_mod.invalidate_permissions_cache()
    invalidate_tool_cache()


@pytest.mark.asyncio
class TestRoleDefaultAdvertisement:
    async def test_driver_list_excludes_unpermitted_tools(self):
        names = _names(await filter_tools_for_role("driver"))
        assert "search_knowledge_base" in names      # open to all roles
        assert "get_vehicle_maintenance" in names     # driver(own) maintenance
        assert "get_driver_efficiency" not in names   # driver lacks can_efficiency

    async def test_unknown_role_returns_all(self):
        names = _names(await filter_tools_for_role("not_a_role"))
        assert "get_account_stats" in names           # defensive full fallback


@pytest.mark.asyncio
class TestScopeAwareAdvertisement:
    async def test_scoped_user_loses_account_wide_tools(self):
        full = _names(await filter_tools_for_role("fleet", scoped=False))
        scoped = _names(await filter_tools_for_role("fleet", scoped=True))
        assert full & ACCOUNT_WIDE_TOOLS          # sanity: some were present
        assert scoped.isdisjoint(ACCOUNT_WIDE_TOOLS)  # all dropped for scoped user
        assert "get_vehicle_detail" in scoped     # vehicle-specific tools remain


@pytest.mark.asyncio
class TestAccountAwareAdvertisement:
    async def test_revoked_feature_drops_its_tools(self, seeded_db, monkeypatch):
        db: Database = seeded_db["db"]
        account = seeded_db["account"]
        monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

        before = _names(await filter_tools_for_role("fleet", account_id=account.id))
        assert "get_maintenance_summary" in before  # fleet default has it

        await db.set_role_permissions(
            account.id, "fleet", {"can_maintenance_all": False},
        )
        perms_mod.invalidate_permissions_cache()

        after = _names(await filter_tools_for_role("fleet", account_id=account.id))
        assert "get_maintenance_summary" not in after  # advertisement reflects revoke


@pytest.mark.asyncio
class TestCacheAndInvalidation:
    async def test_cache_keyed_by_account_role_scope(self):
        a = await get_anthropic_tools(role="fleet", account_id=1, scoped=False)
        b = await get_anthropic_tools(role="fleet", account_id=1, scoped=False)
        assert a is b                       # same key → cached object
        c = await get_anthropic_tools(role="fleet", account_id=1, scoped=True)
        assert c is not a                   # scope is part of the key

    async def test_invalidate_by_account_clears_only_that_account(self):
        a1 = await get_anthropic_tools(role="fleet", account_id=1)
        a2 = await get_anthropic_tools(role="fleet", account_id=2)
        invalidate_tool_cache(1)
        a1b = await get_anthropic_tools(role="fleet", account_id=1)
        a2b = await get_anthropic_tools(role="fleet", account_id=2)
        assert a1b is not a1                # account 1 rebuilt
        assert a2b is a2                    # account 2 untouched
