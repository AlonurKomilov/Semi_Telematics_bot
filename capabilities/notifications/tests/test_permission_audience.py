"""Who is eligible is the ACCOUNT's answer, not the code's default.

Every other gate — the dashboard, the API, alert delivery — resolves a
user's EFFECTIVE permissions: the account's Role Permissions matrix and
module masking layered over the role, in the tier that person is
actually in. Billing notices resolved ``get_permissions(role)`` instead,
whose own docstring calls that "a silent authorization bypass" for
anything deciding what a user may do or see.

So revoking Billing in the matrix hid the pages and refused the API
while the notices kept arriving — and because the category is
registered ``mandatory``, the recipient could not even switch them off.

These pin both directions and the tier, because a rule that is only
tested in the "denied" direction becomes a rule that denies everyone.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.notifications.categories import get_category
from capabilities.notifications.service import _holders_of
from capabilities.permissions.roles import (
    Role, get_permissions, invalidate_permissions_cache)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _platform_is_the_test_db(pg_db, monkeypatch):
    """The resolver reads through ``get_platform_db()``, not the fixture
    handed to the test — so without this a write here is invisible to
    the code under test and every assertion silently measures the
    hardcoded defaults instead of the account's answer."""
    import infra.platform as plat
    monkeypatch.setattr(plat, "_db", pg_db, raising=False)


async def _override(db, account_id: int, role: str, **flags):
    """Write the account's matrix row for one role, seeded from the
    role's own defaults so only the named flags differ."""
    base = get_permissions(Role.from_str(role))
    perms = {k: getattr(base, k) for k in dir(base)
             if k.startswith("can_") and isinstance(getattr(base, k), bool)}
    perms.update(flags)
    await db.set_role_permissions(account_id, role, perms)
    # The resolver caches per (account, role) with a TTL; the Owner-save
    # path invalidates on write, so a test that skips it is testing a
    # stale cache rather than the rule.  (Test account ids repeat across
    # the suite, which is precisely how a stale entry looks like a valid
    # hit — tests/CLAUDE.md.)
    invalidate_permissions_cache(account_id)


class TestBillingDeclaresRatherThanComputes:

    def test_the_category_states_the_permission_it_needs(self):
        import capabilities.notifications          # noqa: F401 — registers
        import capabilities.platform.billing.notifications  # noqa: F401
        cat = get_category("system.billing")
        assert cat.requires_permission == "can_manage_billing"
        # And no closure remains to read the wrong source of truth.
        assert cat.audience is None
        # Still mandatory — which is exactly why getting the audience
        # wrong mattered: an unsilenceable notice to the wrong person.
        assert cat.mandatory is True


class TestTheAccountsAnswerWins:

    async def test_revoking_in_the_matrix_stops_the_notices(self, pg_db):
        """The reported failure, in one assertion: an admin the owner
        removed from Billing must stop receiving billing notices."""
        acct = await pg_db.create_account("Revoke Co")
        admin = await pg_db.create_user(telegram_id=7601, account_id=acct.id,
                                        role=Role.ADMIN)
        # Code default says an admin may manage billing...
        assert get_permissions(Role.ADMIN).can_manage_billing is True
        # ...this account says otherwise.
        await _override(pg_db, acct.id, "admin", can_manage_billing=False)

        tiers = await pg_db.get_permission_tiers_for_users([admin.id])
        holders = await _holders_of(pg_db, tiers, "can_manage_billing")
        assert admin.id not in holders

    async def test_granting_in_the_matrix_starts_them(self, pg_db):
        """The other direction, which a deny-only rule would break: a
        role the CODE says has no billing, but this account granted."""
        acct = await pg_db.create_account("Grant Co")
        disp = await pg_db.create_user(telegram_id=7602, account_id=acct.id,
                                       role=Role.DISPATCHER)
        assert get_permissions(Role.DISPATCHER).can_manage_billing is False
        await _override(pg_db, acct.id, "dispatcher", can_manage_billing=True)

        tiers = await pg_db.get_permission_tiers_for_users([disp.id])
        holders = await _holders_of(pg_db, tiers, "can_manage_billing")
        assert disp.id in holders

    async def test_one_account_does_not_answer_for_another(self, pg_db):
        """The tier key carries the account, so a revocation in one
        account cannot silence another's notices."""
        a = await pg_db.create_account("Tenant A")
        b = await pg_db.create_account("Tenant B")
        ua = await pg_db.create_user(telegram_id=7603, account_id=a.id,
                                     role=Role.ADMIN)
        ub = await pg_db.create_user(telegram_id=7604, account_id=b.id,
                                     role=Role.ADMIN)
        await _override(pg_db, a.id, "admin", can_manage_billing=False)

        tiers = await pg_db.get_permission_tiers_for_users([ua.id, ub.id])
        holders = await _holders_of(pg_db, tiers, "can_manage_billing")
        assert ua.id not in holders          # A revoked it
        assert ub.id in holders              # B did not


class TestFailOpen:

    async def test_an_unresolvable_permission_keeps_the_recipient(self, pg_db):
        """A scoping bug must never silently swallow notifications —
        least of all billing ones, which are mandatory precisely because
        a payment problem has to reach somebody."""
        acct = await pg_db.create_account("Fail Open Co")
        u = await pg_db.create_user(telegram_id=7605, account_id=acct.id,
                                    role=Role.ADMIN)
        tiers = await pg_db.get_permission_tiers_for_users([u.id])
        # A permission that does not exist on the FeatureSet resolves to
        # False, not an error — so assert the ERROR path explicitly with
        # a role the resolver cannot parse.
        broken = {u.id: ("not-a-real-role", False, False, acct.id)}
        assert u.id in await _holders_of(pg_db, broken, "can_manage_billing")
        # And the healthy path still discriminates.
        assert u.id in await _holders_of(pg_db, tiers, "can_manage_billing")

    async def test_an_unknown_flag_denies_rather_than_crashing(self, pg_db):
        acct = await pg_db.create_account("Unknown Flag Co")
        u = await pg_db.create_user(telegram_id=7606, account_id=acct.id,
                                    role=Role.ADMIN)
        tiers = await pg_db.get_permission_tiers_for_users([u.id])
        assert u.id not in await _holders_of(pg_db, tiers, "can_ride_a_horse")
