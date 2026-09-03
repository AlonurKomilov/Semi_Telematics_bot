"""The three surviving helpers of the pair-death pre-flight."""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.fold import builtin_width, seed_for_key, system_trail_context
from capabilities.permissions.roles import ROLE_PERMISSIONS, Role


def test_builtin_defaults():
    assert builtin_width("driver") == "assigned"
    for r in Role:
        if r is not Role.DRIVER:
            assert builtin_width(r.value) == "all"


def test_seed_for_key_shapes():
    assert seed_for_key("owner") is None and seed_for_key("owner__co") is None
    assert seed_for_key("nonsense") is None
    assert seed_for_key("fleet") is ROLE_PERMISSIONS[Role.FLEET]
    assert seed_for_key("recruiter__manager") is not None


class TestSystemTrailContract:
    """The trail records PEOPLE: an event with no actor must declare
    context={'system': '<why>'}.  The first crumb sweep wrote eleven
    grant changes and every trail write raised on exactly this."""

    @pytest.mark.asyncio
    async def test_system_context_is_accepted_and_its_absence_refused(self, pg_db):
        acct = (await pg_db.create_account("Trail Contract Co")).id
        ok = {"entity_type": "role", "entity_id": "recruiter",
              "action": "stale_grant_crumbs_swept", "actor_user_id": None,
              "context": system_trail_context("verb/scope migration hygiene", company_id=None)}
        await pg_db.append_activity_events(acct, [ok])
        with pytest.raises(ValueError):
            await pg_db.append_activity_events(acct, [dict(ok, context={"company_id": None})])
