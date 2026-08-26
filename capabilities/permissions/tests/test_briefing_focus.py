"""The AI briefing focus is derived from each role's EFFECTIVE permissions
(``briefing_focus_for_account`` + the flag-keyed ``BRIEFING_TOPICS`` table) —
not from a hardcoded role→topics map.  That gives every role (current or
future) its own briefing, honours per-account Permissions-matrix overrides,
and picks up new features automatically once they add a table line.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage import Database
from capabilities.permissions.roles import (
    BRIEFING_TOPICS,
    FeatureSet,
    briefing_focus_for_account,
)
import capabilities.permissions.roles as perms_mod

MAINT = "pending and overdue maintenance"
HIRING = "the driver-application (hiring) pipeline"
MOVEMENT = "current vehicle movement and locations"
FUEL_SPEND = "fuel spend"


@pytest.fixture(autouse=True)
def _clear_perms_cache():
    perms_mod.invalidate_permissions_cache()
    yield
    perms_mod.invalidate_permissions_cache()


def test_every_topic_flag_exists_on_featureset():
    """A table entry keyed by a nonexistent flag is dead weight (getattr
    default False silently never matches) — catch it at test time."""
    fields = set(FeatureSet.__dataclass_fields__)
    for flag, _topic in BRIEFING_TOPICS:
        assert flag in fields, f"BRIEFING_TOPICS references unknown flag {flag}"


@pytest.mark.asyncio
async def test_roles_get_distinct_permission_derived_focus(seeded_db, monkeypatch):
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    fleet = await briefing_focus_for_account("fleet", account.id)
    dispatcher = await briefing_focus_for_account("dispatcher", account.id)
    recruiter = await briefing_focus_for_account("recruiter", account.id)
    accounting = await briefing_focus_for_account("accounting", account.id)

    assert MAINT in fleet                       # fleet cares about maintenance
    assert MAINT not in dispatcher              # dispatcher does not (defaults)
    assert MOVEMENT in dispatcher               # dispatcher: movement/locations
    assert HIRING in recruiter                  # recruiter: hiring pipeline
    assert HIRING not in fleet                  # ...which fleet doesn't get
    assert FUEL_SPEND in accounting             # accounting: costs
    # No duplicate topics despite *_all/*_vehicle both mapping to one topic.
    for topics in (fleet, dispatcher, recruiter, accounting):
        assert len(topics) == len(set(topics))


@pytest.mark.asyncio
async def test_matrix_override_removes_topic(seeded_db, monkeypatch):
    """Owner disables Maintenance for fleet → the topic leaves fleet's
    briefing too — one source of truth with the tool gate."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    await db.set_role_permissions(account.id, "fleet", {
        "can_maintenance_all": False, "can_maintenance_vehicle": False,
    })
    perms_mod.invalidate_permissions_cache()

    topics = await briefing_focus_for_account("fleet", account.id)
    assert MAINT not in topics


@pytest.mark.asyncio
async def test_granting_a_flag_adds_topic_no_code_change(seeded_db, monkeypatch):
    """The future-feature contract: grant a role a permission (as a new or
    existing feature would) and its briefing picks the topic up with zero
    per-role code — here, dispatcher gains the hiring pipeline."""
    db: Database = seeded_db["db"]
    account = seeded_db["account"]
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: db)

    assert HIRING not in await briefing_focus_for_account("dispatcher", account.id)

    await db.set_role_permissions(account.id, "dispatcher", {
        "can_manage_applications": True,
    })
    perms_mod.invalidate_permissions_cache()

    assert HIRING in await briefing_focus_for_account("dispatcher", account.id)


@pytest.mark.asyncio
async def test_unknown_role_returns_empty():
    assert await briefing_focus_for_account("nosuchrole", 1) == []
    assert await briefing_focus_for_account("", 1) == []
