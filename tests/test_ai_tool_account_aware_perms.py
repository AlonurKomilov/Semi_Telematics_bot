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


def test_apply_persona_preview_relabels_role_no_escalation():
    """``_apply_persona_preview`` sets the gating role to the previewed one.

    ``view`` is already escalation-checked by ``active_view`` (owner/admin only,
    never a higher role), so this just relabels.  No-op when not previewing.
    """
    from capabilities.ai.router import _apply_persona_preview

    ctx = {"role": "owner"}
    _apply_persona_preview(ctx, "fleet")
    assert ctx["role"] == "fleet"          # owner "View as Fleet" → gated as fleet

    ctx = {"role": "owner"}
    _apply_persona_preview(ctx, "owner")
    assert ctx["role"] == "owner"          # previewing own role → unchanged

    ctx = {"role": "fleet"}
    _apply_persona_preview(ctx, "")
    assert ctx["role"] == "fleet"          # no view header → unchanged

    _apply_persona_preview(None, "fleet")  # None-safe, must not raise


@pytest.mark.asyncio
async def test_owner_preview_as_fleet_gated_as_fleet(seeded_db, monkeypatch):
    """Owner "View as Fleet" is a FAITHFUL preview: when the route applies the
    persona (``user_context['role'] = 'fleet'``), the AI gate enforces Fleet's
    per-account permissions — so disabling Maintenance for Fleet blocks the
    owner-as-Fleet maintenance tool too.  De-scoping, never escalation."""
    from capabilities.ai.router import _apply_persona_preview

    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    await db.set_role_permissions(account.id, "fleet", {"can_maintenance_all": False})
    perms_mod.invalidate_permissions_cache()

    # Owner really, but previewing as Fleet via the dashboard "View as" switch.
    ctx = {"role": "owner"}
    _apply_persona_preview(ctx, "fleet")

    blocked = await _check_tool_permission(
        "get_maintenance_summary", {}, ctx["role"], ctx, account_id=account.id,
    )
    assert blocked is not None                 # gated as Fleet → blocked
    assert "Access denied" in blocked["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role,perm,tool", [
    ("dispatcher", "can_rolling_stopped",      "get_rolling_stopped"),
    ("accounting", "can_fuel_cost",            "get_fuel_cost_summary"),
    ("hr",         "can_manage_applications",  "get_driver_applications"),
    ("safety",     "can_events_all",           "get_events_summary"),
])
async def test_persona_preview_gates_every_role(seeded_db, monkeypatch, role, perm, tool):
    """The persona preview is role-AGNOSTIC: owner "View as <role>" gates the AI
    by that role's per-account permissions for dispatch / accounting / hr /
    safety alike (not just fleet) — enabled → allowed, disabled → blocked.
    Same account-aware gate, no per-role code."""
    from capabilities.ai.router import _apply_persona_preview

    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    ctx = {"role": "owner"}
    _apply_persona_preview(ctx, role)
    assert ctx["role"] == role  # owner is gated as the previewed role

    # Enabled for this role on this account → the previewed AI may use the tool.
    await db.set_role_permissions(account.id, role, {perm: True})
    perms_mod.invalidate_permissions_cache()
    assert await _check_tool_permission(tool, {}, ctx["role"], ctx, account_id=account.id) is None

    # Disabled in the matrix → the previewed AI is refused.
    await db.set_role_permissions(account.id, role, {perm: False})
    perms_mod.invalidate_permissions_cache()
    blocked = await _check_tool_permission(tool, {}, ctx["role"], ctx, account_id=account.id)
    assert blocked is not None
    assert "Access denied" in blocked["error"]


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
