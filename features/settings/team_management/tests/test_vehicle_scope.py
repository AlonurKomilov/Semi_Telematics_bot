"""Team Management's second scope question: which UNITS.

The verb/scope migration contract (capabilities/permissions/
taxonomy.py) sends "which units does this member see" here — the
*_vehicle permission flags will die into a view verb plus THIS.  These
tests pin the semantics before any enforcement site consumes them, so
stage D inherits a settled answer instead of negotiating one mid-move.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from adapters.storage.models import User
from capabilities.permissions.roles import Role


def _member(role: Role, scope: str | None = None) -> User:
    return User(
        id=1, telegram_id=1, account_id=1, role=role,
        truck_num=None, alerts_on=False, is_active=True,
        created_at="", vehicle_scope=scope,
    )


class TestTheSinglePredicate:
    """``User.resolved_vehicle_scope`` — override wins, else role
    default, unknown fails closed.  The model answers so every layer
    (API deps, bot, feature routers) resolves identically."""

    def test_driver_defaults_to_assigned(self):
        assert _member(Role.DRIVER).resolved_vehicle_scope == "assigned"

    def test_everyone_else_defaults_to_all(self):
        # The whole enum minus DRIVER, so a role added next year is
        # covered the day it exists instead of silently untested.
        for role in Role:
            if role is Role.DRIVER:
                continue
            assert _member(role).resolved_vehicle_scope == "all", role

    def test_an_explicit_override_beats_the_role_default(self):
        # Both directions: a scoped-down fleet member, a widened driver.
        assert _member(Role.FLEET, "assigned").resolved_vehicle_scope == "assigned"
        assert _member(Role.DRIVER, "all").resolved_vehicle_scope == "all"

    def test_garbage_in_the_column_fails_closed_for_a_driver(self):
        assert _member(Role.DRIVER, "everything").resolved_vehicle_scope == "assigned"

    def test_garbage_falls_back_to_the_role_default_not_wide(self):
        # A non-driver with a corrupt value gets their ROLE default —
        # which is 'all' for fleet.  The fail-closed guarantee is that
        # garbage never GRANTS beyond what the role would have had.
        assert _member(Role.FLEET, "everything").resolved_vehicle_scope == "all"


class TestStorageRoundTrip:
    @pytest.mark.asyncio
    async def test_override_set_cleared_and_validated(self, pg_db):
        acct = (await pg_db.create_account("Scope Co")).id
        u = await pg_db.create_user(9001, acct, role=Role.FLEET)

        assert (await pg_db.get_user_by_id(u.id)).resolved_vehicle_scope == "all"

        assert await pg_db.set_user_vehicle_scope(acct, u.id, "assigned")
        assert (await pg_db.get_user_by_id(u.id)).vehicle_scope == "assigned"
        assert (await pg_db.get_user_by_id(u.id)).resolved_vehicle_scope == "assigned"

        assert await pg_db.set_user_vehicle_scope(acct, u.id, None)
        assert (await pg_db.get_user_by_id(u.id)).vehicle_scope is None

        with pytest.raises(ValueError):
            await pg_db.set_user_vehicle_scope(acct, u.id, "everything")

    @pytest.mark.asyncio
    async def test_the_wall_between_accounts_holds_on_write(self, pg_db):
        # Setting scope on another account's member must be a no-op —
        # the same account_id-in-WHERE discipline every write here has.
        acct_a = (await pg_db.create_account("Scope A")).id
        acct_b = (await pg_db.create_account("Scope B")).id
        u_b = await pg_db.create_user(9002, acct_b, role=Role.FLEET)
        assert not await pg_db.set_user_vehicle_scope(acct_a, u_b.id, "assigned")
        assert (await pg_db.get_user_by_id(u_b.id)).vehicle_scope is None


class TestEveryResolverUsesTheModel:
    """The company wall's 'every resolver uses company_allows' rule,
    applied to the second scope question: the API adapter must resolve
    through the model property, never re-derive from the role."""

    def test_api_resolver_rides_resolved_vehicle_scope(self):
        import inspect
        from interfaces.api import deps
        src = inspect.getsource(deps.get_member_vehicle_scope)
        assert "resolved_vehicle_scope" in src
        # Re-deriving would mention the driver role; the adapter must not.
        assert "DRIVER" not in src and "driver" not in src.replace(
            "get_member_vehicle_scope", "").replace("db_user", "")


class TestTheBridgeHelperAsksTwoDifferentQuestions:
    """``member_unit_scope`` is NOT ``get_member_vehicle_scope`` with a
    grant check bolted on — the two ask different questions and must
    default in OPPOSITE directions.  Enforcement found this: wiring
    the first family narrowed a wide-granted owner to nothing whenever
    the member row (or the whole platform) could not be read.

      * "what is this member's scope?" — unknown → 'assigned', the
        cautious answer, because we are describing a person.
      * "should this WIDE-granted request be narrowed?" — unknown
        means no override is KNOWN, and inventing one deletes a
        legitimate caller's data.  The grant is authoritative during
        the bridge, so unknown → 'all'.
    """

    @pytest.mark.asyncio
    async def test_no_platform_falls_back_to_the_grant_not_to_assigned(self):
        from interfaces.api.deps import member_unit_scope
        # Owner: wide grant from seeds, no infra booted in this test —
        # the helper must not narrow.
        got = await member_unit_scope(
            {"role": "owner", "account_id": 1}, "maintenance")
        assert got == "all"

    @pytest.mark.asyncio
    async def test_a_narrow_grant_still_narrows_without_any_lookup(self):
        from interfaces.api.deps import member_unit_scope
        # Driver: the seeded grant is vehicle-only, so the answer is
        # 'assigned' from the grant alone — no member row needed.
        got = await member_unit_scope(
            {"role": "driver", "account_id": 1}, "maintenance")
        assert got == "assigned"

    @pytest.mark.asyncio
    async def test_the_sibling_question_keeps_its_cautious_default(self):
        from interfaces.api.deps import get_member_vehicle_scope
        assert await get_member_vehicle_scope(
            {"role": "owner", "account_id": 1}) == "assigned"
