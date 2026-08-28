"""Integration tests for capabilities.alerting.on_shift.

Covers the role→persona resolution and the on-shift filtering used by
the per-persona-groups CRITICAL @-mention path.  The DND/work_hours
logic itself is owned by ``capabilities.alerting.dnd`` — we mock it
out here so the test exercises ONLY the persona-roles join.
"""

from __future__ import annotations

import pytest

from adapters.storage.models import Role
from capabilities.alerting import on_shift, persona_mapping


def test_roles_for_persona_returns_owner_and_admin_for_owner_admin():
    roles = on_shift.roles_for_persona(persona_mapping.OWNER_ADMIN)
    assert set(roles) == {Role.OWNER, Role.ADMIN}


def test_roles_for_persona_returns_single_role_for_operational_personas():
    assert on_shift.roles_for_persona(persona_mapping.DISPATCHER) == (Role.DISPATCHER,)
    assert on_shift.roles_for_persona(persona_mapping.SAFETY) == (Role.SAFETY,)
    assert on_shift.roles_for_persona(persona_mapping.FLEET) == (Role.FLEET,)
    assert on_shift.roles_for_persona(persona_mapping.HR) == (Role.HR,)


def test_roles_for_unknown_persona_returns_empty_tuple():
    assert on_shift.roles_for_persona("not_a_persona") == ()


def test_format_mentions_empty_users_returns_empty_string():
    assert on_shift.format_mentions([]) == ""


def test_format_mentions_renders_tg_user_link_html():
    user = on_shift.OnShiftUser(
        user_id=1, telegram_id=999, display_name="Alice",
    )
    out = on_shift.format_mentions([user])
    assert 'href="tg://user?id=999"' in out
    assert ">Alice</a>" in out


def test_format_mentions_escapes_html_in_display_name():
    user = on_shift.OnShiftUser(
        user_id=1, telegram_id=999, display_name="<script>x</script>",
    )
    out = on_shift.format_mentions([user])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_format_mentions_caps_to_max_mentions():
    users = [
        on_shift.OnShiftUser(user_id=i, telegram_id=100 + i, display_name=f"u{i}")
        for i in range(10)
    ]
    out = on_shift.format_mentions(users, max_mentions=3)
    assert out.count('href="tg://user?id=') == 3


def test_format_mentions_substitutes_default_for_blank_name():
    user = on_shift.OnShiftUser(
        user_id=1, telegram_id=999, display_name="",
    )
    out = on_shift.format_mentions([user])
    assert "On-shift" in out


@pytest.mark.asyncio
async def test_users_on_shift_for_unknown_persona_returns_empty(monkeypatch):
    """Unknown persona ⇒ empty role tuple ⇒ no DB query ⇒ []."""
    result = await on_shift.users_on_shift_for_persona(1, "ghost_persona")
    assert result == []


@pytest.mark.asyncio
async def test_users_on_shift_filters_to_persona_and_account(
    monkeypatch, seeded_db, db,
):
    """Sanity end-to-end: subscribers in the wrong account / role do not
    appear in the persona's on-shift list."""
    acct = seeded_db["account"]
    other = await db.create_account("Other Fleet")

    # Owner of seeded acct (Role.OWNER) — wants alerts ON.
    await db.update_user(seeded_db["owner"].id, alerts_on=True)

    # Wrong-account dispatcher: must be excluded by account filter.
    wrong_acct_disp = await db.create_user(
        telegram_id=200001, account_id=other.id, role=Role.DISPATCHER,
    )
    await db.update_user(wrong_acct_disp.id, alerts_on=True)

    # Right-account, wrong-role user: must be excluded by role filter.
    safety_user = await db.create_user(
        telegram_id=200002, account_id=acct.id, role=Role.SAFETY,
    )
    await db.update_user(safety_user.id, alerts_on=True)

    # Right-account dispatcher: SHOULD appear.
    right_disp = await db.create_user(
        telegram_id=200003, account_id=acct.id, role=Role.DISPATCHER,
    )
    await db.update_user(right_disp.id, alerts_on=True)

    # Wire infra.platform and infra.services to the test DB.
    import infra.platform as platform_mod
    import infra.services as services_mod
    monkeypatch.setattr(platform_mod, "get_platform_db", lambda: db)

    async def _fake_get_tenant(_aid):
        return db
    monkeypatch.setattr(services_mod, "get_tenant_db", _fake_get_tenant)
    # Also override the alias on_shift imported.
    monkeypatch.setattr(on_shift, "get_platform_db", lambda: db)
    monkeypatch.setattr(on_shift, "get_tenant_db", _fake_get_tenant)

    # Force everyone "on-shift" so the persona filter is what matters.
    async def _always_on_shift(_user, _tenant):
        return True
    import capabilities.alerting.dnd as dnd_mod
    monkeypatch.setattr(dnd_mod, "is_user_on_shift", _always_on_shift)

    result = await on_shift.users_on_shift_for_persona(
        acct.id, persona_mapping.DISPATCHER,
    )
    tg_ids = {u.telegram_id for u in result}
    assert tg_ids == {200003}  # only the right-account dispatcher
