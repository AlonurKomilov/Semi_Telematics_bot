"""AI gates resolve the USER's permissions — role AND tier.

Three AI sites (the attachment import gate, the briefing enrichment, and
tool execution) called ``get_account_permissions``, whose own docstring
reserves it for "account-agnostic, role-level surfaces".  The effect was
wrong in both directions:

* a Fleet MANAGER whose owner had restricted plain fleet users and
  granted managers was told "Your role can't run imports" while the
  Permissions page showed the import allowed — the page edits the
  ``fleet__manager`` row, the gate read the base ``fleet`` row;
* a CO-OWNER the owner had restricted resolved as the full primary owner.

Also closed on the way: an unparseable role string used to fall through
tool-permission enforcement as ALLOWED (the except branch logged and
returned nothing).  It denies now.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.roles import FeatureSet


def _fs(**flags) -> FeatureSet:
    return FeatureSet(**flags)


async def test_resolver_hands_the_tier_flags_to_get_user_permissions(monkeypatch):
    import capabilities.permissions.roles as R
    from capabilities.ai.usage import resolve_user_permissions

    seen: dict = {}

    async def fake_get_user_permissions(role, account_id, is_manager=False,
                                        is_primary_owner=False, company_id=None):
        seen.update(role=role.value, account_id=account_id,
                    is_manager=is_manager, is_primary_owner=is_primary_owner)
        return _fs()

    monkeypatch.setattr(R, "get_user_permissions", fake_get_user_permissions)

    await resolve_user_permissions(
        "fleet", 7, {"is_manager": True, "is_primary_owner": False},
    )
    assert seen == {"role": "fleet", "account_id": 7,
                    "is_manager": True, "is_primary_owner": False}

    # A missing context is the base tier, never an error.
    seen.clear()
    await resolve_user_permissions("fleet", 7, None)
    assert seen["is_manager"] is False and seen["is_primary_owner"] is False


async def test_resolver_returns_none_for_an_unknown_role():
    from capabilities.ai.usage import resolve_user_permissions
    assert await resolve_user_permissions("not-a-role", 7, {}) is None
    assert await resolve_user_permissions(None, 7, {}) is None


async def test_import_gate_honours_the_manager_tier(monkeypatch):
    """The reported case.  Base fleet: no can_manage_vehicles.  Manager
    tier: has it.  The gate must pass a manager and refuse a plain fleet
    user — reading the row the Permissions page actually edited."""
    import capabilities.ai.attachments as A
    import capabilities.permissions.roles as R

    async def fake_get_user_permissions(role, account_id, is_manager=False,
                                        is_primary_owner=False, company_id=None):
        return _fs(can_manage_vehicles=bool(is_manager))

    monkeypatch.setattr(R, "get_user_permissions", fake_get_user_permissions)

    async def _noop(*a, **k):
        return [], []

    monkeypatch.setattr(A, "_IMPORT_TARGETS", {})
    A.register_import_target(A.ImportTarget(
        name="inv", description="", fields={},
        build_rows=_noop, executor=_noop, permission="can_manage_vehicles",
    ))

    class Sheet:
        name = "sheet.csv"
        content = "a,b\n1,2\n"

    grids, _, _ = await A.parse_attachments_for_request(
        [Sheet()], "fleet", 7, user_context={"is_manager": True},
    )
    assert grids["sheet.csv"] == [["a", "b"], ["1", "2"]]

    with pytest.raises(A.AttachmentError, match="can't run imports"):
        await A.parse_attachments_for_request(
            [Sheet()], "fleet", 7, user_context={"is_manager": False},
        )


async def test_tool_execution_honours_the_manager_tier(monkeypatch):
    """Fixing the gate alone would let the sheet parse and then fail at
    approve — the execution check had the same bug."""
    import capabilities.ai.intelligence as I
    import capabilities.permissions.roles as R

    async def fake_get_user_permissions(role, account_id, is_manager=False,
                                        is_primary_owner=False, company_id=None):
        return _fs(can_manage_vehicles=bool(is_manager))

    monkeypatch.setattr(R, "get_user_permissions", fake_get_user_permissions)
    monkeypatch.setattr(R, "TOOL_PERMISSIONS",
                        {**R.TOOL_PERMISSIONS, "tier_tool": ["can_manage_vehicles"]})
    monkeypatch.setattr(I, "TOOL_PERMISSIONS", R.TOOL_PERMISSIONS)

    ok = await I._check_tool_permission("tier_tool", {}, "fleet",
                                        {"is_manager": True}, 7)
    assert ok is None, ok
    blocked = await I._check_tool_permission("tier_tool", {}, "fleet",
                                             {"is_manager": False}, 7)
    assert blocked and "Access denied" in blocked["error"]


async def test_tool_execution_denies_an_unparseable_role(monkeypatch):
    """The old except-branch logged and fell through to ALLOWED."""
    import capabilities.ai.intelligence as I
    import capabilities.permissions.roles as R
    monkeypatch.setattr(R, "TOOL_PERMISSIONS",
                        {**R.TOOL_PERMISSIONS, "tier_tool": ["can_manage_vehicles"]})
    monkeypatch.setattr(I, "TOOL_PERMISSIONS", R.TOOL_PERMISSIONS)
    blocked = await I._check_tool_permission("tier_tool", {}, "??", {}, 7)
    assert blocked and "Access denied" in blocked["error"]


def test_preview_is_the_base_tier_of_the_previewed_role():
    """A full admin previewing "fleet" must not resolve the fleet MANAGER
    row — the preview promises it only ever narrows."""
    from capabilities.ai.router import _apply_persona_preview
    ctx = {"role": "admin", "is_manager": True, "is_primary_owner": False}
    _apply_persona_preview(ctx, "fleet")
    assert ctx["role"] == "fleet"
    assert ctx["preview_active"] is True
    assert ctx["is_manager"] is False and ctx["is_primary_owner"] is False


def test_user_context_carries_the_tier_flags():
    from dataclasses import dataclass
    from capabilities.ai.usage import build_user_ai_context

    @dataclass
    class U:
        role: str = "fleet"
        display_name: str = "Cody Brown"
        truck_num: str = ""
        timezone: str = "America/Chicago"
        is_manager: bool = True
        is_primary_owner: bool = False

    ctx = build_user_ai_context(U())
    assert ctx["is_manager"] is True and ctx["is_primary_owner"] is False


@pytest.mark.asyncio
class TestManagerTierAdvertisement:
    """The reported case, against a real permission store.

    Owner restricts plain fleet users (no Vehicles → Manage) and grants it
    to fleet MANAGERS.  The manager must be OFFERED the import tool; the
    plain fleet user must not — and the two must never share a cache
    entry.
    """

    async def test_manager_sees_the_tool_the_base_tier_lacks(self, seeded_db, monkeypatch):
        import features.vehicles.inventory.ai_actions  # noqa: F401 — registers the tool
        import capabilities.permissions.roles as R
        from capabilities.ai.tools.registry import (
            filter_tools_for_role, get_cached_vertex_tools, invalidate_tool_cache,
        )
        db = seeded_db["db"]
        account = seeded_db["account"]
        monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

        await db.set_role_permissions(account.id, "fleet", {"can_manage_vehicles": False})
        await db.set_role_permissions(
            account.id, R.perm_tier_key("fleet", True), {"can_manage_vehicles": True},
        )
        R.invalidate_permissions_cache()
        invalidate_tool_cache()

        def names(defs):
            return {d["name"] for d in defs}

        plain = names(await filter_tools_for_role(
            "fleet", account_id=account.id, user_context={"is_manager": False}))
        manager = names(await filter_tools_for_role(
            "fleet", account_id=account.id, user_context={"is_manager": True}))
        assert "import_inventory_items" not in plain
        assert "import_inventory_items" in manager, sorted(manager)

        # The CACHED wrapper must key on the tier, or whichever tier asks
        # second gets the first one's list.
        first = await get_cached_vertex_tools(
            role="fleet", account_id=account.id, user_context={"is_manager": False})
        second = await get_cached_vertex_tools(
            role="fleet", account_id=account.id, user_context={"is_manager": True})
        assert first is not second


def test_no_ai_gate_uses_the_role_level_resolver():
    """Structural guard.  Four sites resolved at ROLE level; the fourth
    was found only by grepping after the first three were fixed.  Any AI
    code that needs a permission set goes through
    ``resolve_user_permissions`` — a direct ``get_account_permissions(``
    call here is the bug coming back."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]          # capabilities/ai
    offenders = []
    for py in root.rglob("*.py"):
        if "tests" in py.parts:
            continue
        for n, line in enumerate(py.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "get_account_permissions(" in code:
                offenders.append(f"{py.relative_to(root)}:{n}")
    assert offenders == [], offenders
