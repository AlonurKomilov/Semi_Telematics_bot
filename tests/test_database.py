"""Tests for the database layer — async SQLite operations.

Uses a temporary in-memory database for isolation.
"""

import os
import pytest
import pytest_asyncio

# Ensure encryption is not active during DB tests (unless explicitly tested)
os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.storage import Role, Account, User, Invite


# ── Fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded_db(db):
    """DB pre-populated with an account, company, and owner user."""
    account = await db.create_account("Test Fleet Co")
    company = await db.add_company(
        account_id=account.id,
        code="TFC",
        samsara_api_key="samsara_api_test_key_123",
        display_name="Test Fleet",
    )
    owner = await db.create_user(
        telegram_id=111111,
        account_id=account.id,
        role=Role.OWNER,
    )
    return db, account, company, owner


# ══════════════════════════════════════════════════════════════════
# ACCOUNTS
# ══════════════════════════════════════════════════════════════════

class TestAccounts:

    @pytest.mark.asyncio
    async def test_create_account(self, db):
        acct = await db.create_account("My Fleet")
        assert isinstance(acct, Account)
        assert acct.name == "My Fleet"
        assert acct.is_active is True
        assert acct.tier == "free"
        assert acct.id > 0

    @pytest.mark.asyncio
    async def test_get_account(self, db):
        acct = await db.create_account("Lookup Test")
        result = await db.get_account(acct.id)
        assert result is not None
        assert result.name == "Lookup Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent_account(self, db):
        result = await db.get_account(99999)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_accounts(self, db):
        await db.create_account("A Co")
        await db.create_account("B Co")
        accounts = await db.list_accounts()
        assert len(accounts) == 2

    @pytest.mark.asyncio
    async def test_update_account(self, db):
        acct = await db.create_account("Original")
        await db.update_account(acct.id, name="Updated")
        updated = await db.get_account(acct.id)
        assert updated.name == "Updated"

    @pytest.mark.asyncio
    async def test_disable_account(self, db):
        acct = await db.create_account("Active Co")
        await db.update_account(acct.id, is_active=False)
        # Active-only listing excludes it
        active = await db.list_accounts(active_only=True)
        assert all(a.id != acct.id for a in active)

    @pytest.mark.asyncio
    async def test_duplicate_account_name_rejected(self, db):
        # Account names are unique case-insensitively — two tenants named
        # the same would be indistinguishable in operator/support surfaces,
        # so create_account refuses the collision (callers surface it as a
        # 409 the signup form renders inline).
        await db.create_account("My Fleet")
        with pytest.raises(ValueError, match="already exists"):
            await db.create_account("my fleet")  # case-insensitive collision

    @pytest.mark.asyncio
    async def test_slug_has_unique_suffix(self, db):
        # Slugs carry a random suffix so even similar names never collide
        # at the slug level (independent of the name-uniqueness rule).
        a1 = await db.create_account("My Fleet")
        a2 = await db.create_account("My Fleet 2")
        assert a1.slug != a2.slug
        assert a1.slug.startswith("my-fleet-")


# ══════════════════════════════════════════════════════════════════
# COMPANIES
# ══════════════════════════════════════════════════════════════════

class TestCompanies:

    @pytest.mark.asyncio
    async def test_add_company(self, seeded_db):
        db, acct, _, _ = seeded_db
        companies = await db.get_account_companies(acct.id)
        assert len(companies) == 1
        assert companies[0].code == "TFC"

    @pytest.mark.asyncio
    async def test_company_code_uppercased(self, seeded_db):
        db, acct, _, _ = seeded_db
        co = await db.add_company(acct.id, "abc", "key123", "Lower Co")
        assert co.code == "ABC"

    @pytest.mark.asyncio
    async def test_get_company_by_code(self, seeded_db):
        db, acct, _, _ = seeded_db
        co = await db.get_company_by_code(acct.id, "TFC")
        assert co is not None
        assert co.display_name == "Test Fleet"

    @pytest.mark.asyncio
    async def test_get_company_by_code_case_insensitive(self, seeded_db):
        db, acct, _, _ = seeded_db
        co = await db.get_company_by_code(acct.id, "tfc")
        assert co is not None

    @pytest.mark.asyncio
    async def test_remove_company_soft_delete(self, seeded_db):
        db, acct, co, _ = seeded_db
        await db.remove_company(co.id, account_id=acct.id)
        active = await db.get_account_companies(acct.id, active_only=True)
        assert len(active) == 0
        # Still in DB with active_only=False
        all_cos = await db.get_account_companies(acct.id, active_only=False)
        assert len(all_cos) == 1

    @pytest.mark.asyncio
    async def test_update_company(self, seeded_db):
        db, _, co, _ = seeded_db
        await db.update_company(co.id, account_id=co.account_id, display_name="New Name")
        updated = await db.get_company_by_code(co.account_id, co.code)
        assert updated.display_name == "New Name"

    @pytest.mark.asyncio
    async def test_duplicate_company_code_raises(self, seeded_db):
        db, acct, _, _ = seeded_db
        with pytest.raises(Exception):  # UNIQUE constraint
            await db.add_company(acct.id, "TFC", "another_key")


# ══════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════

class TestUsers:

    @pytest.mark.asyncio
    async def test_create_user(self, seeded_db):
        db, acct, _, _ = seeded_db
        user = await db.create_user(222222, acct.id, Role.DRIVER, truck_num="T-100")
        assert isinstance(user, User)
        assert user.role == Role.DRIVER
        assert user.truck_num == "T-100"

    @pytest.mark.asyncio
    async def test_get_user_by_telegram_id(self, seeded_db):
        db, _, _, owner = seeded_db
        user = await db.get_user_by_telegram_id(111111)
        assert user is not None
        assert user.id == owner.id
        assert user.role == Role.OWNER

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, db):
        result = await db.get_user_by_telegram_id(999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_account_users(self, seeded_db):
        db, acct, _, _ = seeded_db
        await db.create_user(222222, acct.id, Role.ADMIN)
        await db.create_user(333333, acct.id, Role.DRIVER)
        users = await db.list_account_users(acct.id)
        assert len(users) == 3  # owner + admin + driver

    @pytest.mark.asyncio
    async def test_update_user_role(self, seeded_db):
        db, acct, _, _ = seeded_db
        fleet = await db.create_user(222222, acct.id, Role.FLEET)
        await db.update_user(fleet.id, role=Role.ADMIN)
        updated = await db.get_user(fleet.id)
        assert updated.role == Role.ADMIN

    @pytest.mark.asyncio
    async def test_remove_user_soft_delete(self, seeded_db):
        db, acct, _, _ = seeded_db
        driver = await db.create_user(222222, acct.id, Role.DRIVER)
        await db.remove_user(driver.id)
        # Should not appear in telegram_id lookup (active only)
        gone = await db.get_user_by_telegram_id(222222)
        assert gone is None

    @pytest.mark.asyncio
    async def test_toggle_alerts(self, seeded_db):
        db, _, _, owner = seeded_db
        assert owner.alerts_on is False
        new_state = await db.toggle_alerts(owner.telegram_id)
        assert new_state is True
        toggled_back = await db.toggle_alerts(owner.telegram_id)
        assert toggled_back is False

    @pytest.mark.asyncio
    async def test_count_users(self, seeded_db):
        db, acct, _, _ = seeded_db
        count = await db.count_all_users()
        assert count >= 1

    @pytest.mark.asyncio
    async def test_user_properties(self, seeded_db):
        db, _, _, owner = seeded_db
        assert owner.is_owner is True
        assert owner.is_admin_or_above is True

        driver = await db.create_user(222222, owner.account_id, Role.DRIVER)
        assert driver.is_owner is False
        assert driver.is_admin_or_above is False


# ══════════════════════════════════════════════════════════════════
# INVITES
# ══════════════════════════════════════════════════════════════════

class TestInvites:

    @pytest.mark.asyncio
    async def test_create_invite(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET, "ops"
        )
        assert isinstance(invite, Invite)
        assert "-" in invite.code  # XXXX-XXXX format
        assert len(invite.code) == 9
        assert invite.is_used is False
        assert invite.is_expired is False

    @pytest.mark.asyncio
    async def test_redeem_invite(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.DISPATCHER,
        )
        new_user = await db.redeem_invite(invite.code, 555555)
        assert new_user is not None
        assert new_user.role == Role.DISPATCHER
        assert new_user.account_id == acct.id

    @pytest.mark.asyncio
    async def test_redeem_invite_marks_as_used(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.DRIVER, truck_num="T-5")
        await db.redeem_invite(invite.code, 555555)
        redeemed = await db.get_invite(invite.code)
        assert redeemed.is_used is True

    @pytest.mark.asyncio
    async def test_cannot_redeem_twice(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        await db.redeem_invite(invite.code, 555555)
        result = await db.redeem_invite(invite.code, 666666)
        assert result is None  # already used

    @pytest.mark.asyncio
    async def test_cannot_redeem_expired(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET, hours=0)
        # hours=0 means expires immediately
        import asyncio
        await asyncio.sleep(0.01)
        result = await db.redeem_invite(invite.code, 555555)
        # Depending on timing, this may or may not be expired.
        # Create truly expired invite by manipulating DB directly
        inv2 = await db.create_invite(acct.id, owner.id, Role.FLEET)
        await db._db.execute(
            "UPDATE invites SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (inv2.id,),
        )
        await db._db.commit()
        result2 = await db.redeem_invite(inv2.code, 666666)
        assert result2 is None

    @pytest.mark.asyncio
    async def test_registered_user_cannot_redeem(self, seeded_db):
        """User already registered cannot redeem an invite."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        # Owner (tid=111111) is already registered
        result = await db.redeem_invite(invite.code, 111111)
        assert result is None

    @pytest.mark.asyncio
    async def test_invite_code_format(self, seeded_db):
        """Codes should be XXXX-XXXX (hex uppercase)."""
        db, acct, _, owner = seeded_db
        for _ in range(10):
            inv = await db.create_invite(acct.id, owner.id, Role.DRIVER)
            parts = inv.code.split("-")
            assert len(parts) == 2
            assert len(parts[0]) == 4
            assert len(parts[1]) == 4

    @pytest.mark.asyncio
    async def test_invite_with_truck_num(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.DRIVER, truck_num="T-42"
        )
        user = await db.redeem_invite(invite.code, 777777)
        assert user.truck_num == "T-42"
        assert user.role == Role.DRIVER


# ══════════════════════════════════════════════════════════════════
# INVITE REVOKE (soft-delete via revoked_at, migration 087)
# ══════════════════════════════════════════════════════════════════

class TestInvitesRevoke:
    """Covers revoke_invite + the redeem-side TOCTOU guard + the
    orthogonal list_invites filters.  These tests are the regression
    contract for migration 087 — touching any of the WHERE clauses on
    invites without re-running them risks silently re-introducing the
    revoke→redeem race the design vet flagged."""

    @pytest.mark.asyncio
    async def test_new_invite_is_not_revoked(self, seeded_db):
        """Sanity check: fresh invites have revoked_at=None and
        ``is_revoked`` reads False.  Locks the dataclass default."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        assert invite.revoked_at is None
        assert invite.is_revoked is False

    @pytest.mark.asyncio
    async def test_revoke_invite_returns_row_and_sets_timestamp(self, seeded_db):
        """Successful revoke returns the row (so the route can write
        forensic audit details) and sets revoked_at to an ISO-8601
        timestamp."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.DRIVER)
        revoked = await db.revoke_invite(acct.id, invite.id)
        assert revoked is not None
        assert revoked.id == invite.id
        assert revoked.role == "driver"
        assert revoked.revoked_at is not None
        # Round-trips through ISO-8601 parser without raising
        from datetime import datetime
        datetime.fromisoformat(revoked.revoked_at)

    @pytest.mark.asyncio
    async def test_get_invite_returns_none_for_revoked(self, seeded_db):
        """Email-signup + bot /join + cmd_start deep-link all flow
        through ``get_invite``; the revoke_at filter hiding revoked
        rows there is the single choke-point that protects every
        redemption surface.  Regressing the WHERE clause would silently
        re-admit revoked codes."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        await db.revoke_invite(acct.id, invite.id)
        assert await db.get_invite(invite.code) is None
        # The admin-side getter MUST still surface the row — the
        # asymmetry between the two getters is intentional and the
        # revoke endpoint needs it for the audit-log capture.
        admin_view = await db.get_invite_by_id(acct.id, invite.id)
        assert admin_view is not None
        assert admin_view.is_revoked is True

    @pytest.mark.asyncio
    async def test_redeem_after_revoke_returns_none(self, seeded_db):
        """End-to-end: revoke first, redeem second, no user created.
        Covers the get_invite filter path (the fast reject).  See the
        race-window test below for the TOCTOU path."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        await db.revoke_invite(acct.id, invite.id)
        result = await db.redeem_invite(invite.code, 888001)
        assert result is None
        # And no user landed in the table
        assert await db.get_user_by_telegram_id(888001) is None

    @pytest.mark.asyncio
    async def test_redeem_invite_loses_revoke_race_raises_and_rolls_back(self, seeded_db):
        """TOCTOU regression: simulate the race where the operator's
        revoke commits AFTER redeem snapshots the invite (via
        get_invite) but BEFORE redeem's guarded UPDATE.

        Mechanism: monkey-patch ``get_invite`` to return the
        pre-revoke snapshot, then revoke the row underneath, then
        let redeem proceed.  The UPDATE-with-guard matches 0 rows,
        raises ``RuntimeError``, and the surrounding transaction
        rolls the user creation back.  If anyone drops the rowcount
        check or the WHERE-clause guard, this test breaks loudly.
        """
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)

        # Hand-roll the race: capture the pre-revoke snapshot, then
        # commit the revoke, then patch get_invite to keep returning
        # the stale snapshot so redeem progresses past its own
        # early-return guards.
        snapshot = await db.get_invite(invite.code)
        await db.revoke_invite(acct.id, invite.id)
        original_get_invite = db.get_invite
        async def _stale_get_invite(code: str):
            return snapshot
        db.get_invite = _stale_get_invite  # type: ignore[assignment]
        try:
            with pytest.raises(RuntimeError, match="race lost"):
                await db.redeem_invite(invite.code, 888002)
        finally:
            db.get_invite = original_get_invite  # type: ignore[assignment]

        # User creation rolled back
        assert await db.get_user_by_telegram_id(888002) is None
        # And the invite's used_by stayed NULL
        admin_view = await db.get_invite_by_id(acct.id, invite.id)
        assert admin_view is not None
        assert admin_view.used_by is None

    @pytest.mark.asyncio
    async def test_revoke_invite_returns_none_for_already_revoked(self, seeded_db):
        """Idempotency: a second revoke against the same id returns
        None (the route surfaces this as 404 without writing a
        duplicate audit-log entry), and the original revoked_at
        timestamp is preserved — we don't overwrite it on retry."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        first = await db.revoke_invite(acct.id, invite.id)
        assert first is not None
        first_ts = first.revoked_at

        second = await db.revoke_invite(acct.id, invite.id)
        assert second is None

        # Original timestamp preserved
        admin_view = await db.get_invite_by_id(acct.id, invite.id)
        assert admin_view is not None
        assert admin_view.revoked_at == first_ts

    @pytest.mark.asyncio
    async def test_revoke_invite_returns_none_for_already_used(self, seeded_db):
        """Once a user has redeemed an invite, the operator must NOT
        be able to flip it to revoked — the audit trail and the
        ``is_revoked`` UI badge would lie about why the row is dead.
        revoke_invite's ``used_by IS NULL`` clause defends this."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        user = await db.redeem_invite(invite.code, 888003)
        assert user is not None

        result = await db.revoke_invite(acct.id, invite.id)
        assert result is None

        # used_by intact, revoked_at still null
        admin_view = await db.get_invite_by_id(acct.id, invite.id)
        assert admin_view is not None
        assert admin_view.used_by == user.id
        assert admin_view.revoked_at is None

    @pytest.mark.asyncio
    async def test_revoke_invite_returns_none_for_unknown_id(self, seeded_db):
        """Unknown id → None.  Covers the same not-found branch as
        cross-account isolation but via a different code path (id
        simply doesn't exist anywhere)."""
        db, acct, _, _ = seeded_db
        assert await db.revoke_invite(acct.id, 99999) is None

    @pytest.mark.asyncio
    async def test_revoke_invite_rejects_cross_account(self, db):
        """Tenant isolation: account B cannot revoke account A's
        invite by guessing its numeric id.  Both the SELECT and the
        UPDATE carry ``AND account_id = ?`` — defense-in-depth so a
        future RLS rollback doesn't silently expose this."""
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        owner_a = await db.create_user(
            telegram_id=900_001, account_id=acct_a.id, role=Role.OWNER,
        )
        invite = await db.create_invite(acct_a.id, owner_a.id, Role.FLEET)
        # Account B tries to revoke it
        assert await db.revoke_invite(acct_b.id, invite.id) is None
        # Now account A can — proves the row exists and the cross-
        # account guard is the thing that rejected B
        revoked = await db.revoke_invite(acct_a.id, invite.id)
        assert revoked is not None
        assert revoked.revoked_at is not None

    @pytest.mark.asyncio
    async def test_list_invites_orthogonal_filters(self, seeded_db):
        """``pending_only`` (hide used) and ``include_revoked``
        (surface revoked) are orthogonal flags.  The dashboard's
        single "Show all" toggle flips BOTH; ensuring they compose
        correctly here is the regression contract for that toggle."""
        db, acct, _, owner = seeded_db
        # A: live (pending, not revoked), B: revoked, C: used
        a = await db.create_invite(acct.id, owner.id, Role.FLEET)
        b = await db.create_invite(acct.id, owner.id, Role.DRIVER)
        await db.revoke_invite(acct.id, b.id)
        c = await db.create_invite(acct.id, owner.id, Role.DISPATCHER)
        await db.redeem_invite(c.code, 901_001)

        # Default (admin "live invites" view): only A
        default = await db.list_invites(acct.id)
        default_ids = {i.id for i in default}
        assert a.id in default_ids
        assert b.id not in default_ids
        assert c.id not in default_ids

        # pending_only=False, include_revoked=False: A + C (used
        # surfaced; revoked still hidden — proves the AND, not OR)
        no_pending = await db.list_invites(
            acct.id, pending_only=False, include_revoked=False,
        )
        no_pending_ids = {i.id for i in no_pending}
        assert {a.id, c.id} == no_pending_ids

        # Full sweep — operator "Show all": A + B + C
        all_three = await db.list_invites(
            acct.id, pending_only=False, include_revoked=True,
        )
        assert {a.id, b.id, c.id} == {i.id for i in all_three}

        # pending_only=True, include_revoked=True: A only (used hidden
        # by pending_only, revoked surfaced but used_by still null on
        # A so it stays); B has used_by NULL too so include_revoked=True
        # surfaces it.  → {A, B}
        pending_with_revoked = await db.list_invites(
            acct.id, pending_only=True, include_revoked=True,
        )
        pending_with_revoked_ids = {i.id for i in pending_with_revoked}
        assert {a.id, b.id} == pending_with_revoked_ids

    # ── extend_invite ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_extend_invite_pushes_expiry_forward(self, seeded_db):
        """Happy path: extend bumps ``expires_at`` to ``now + hours``
        and returns the row.  Tests both the anchor-to-now semantic
        (not anchor-to-old-expiry) and the same-code preservation
        promise (any chat-history copy of the link remains valid)."""
        db, acct, _, owner = seeded_db
        from datetime import datetime, timezone
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET, hours=1)
        original_code = invite.code
        original_expires = datetime.fromisoformat(invite.expires_at)

        extended = await db.extend_invite(acct.id, invite.id, hours=24)
        assert extended is not None
        # Code unchanged — the chat-history-copy contract
        assert extended.code == original_code
        # New expiry is at least 23h in the future from now (loose
        # lower bound to absorb test-execution drift on a slow CI box)
        new_expires = datetime.fromisoformat(extended.expires_at)
        delta_from_now = new_expires - datetime.now(timezone.utc)
        assert delta_from_now.total_seconds() > 23 * 3600
        # And strictly later than the original — never goes backwards
        assert new_expires > original_expires

    @pytest.mark.asyncio
    async def test_extend_invite_anchors_to_now_not_existing_expiry(
        self, seeded_db,
    ):
        """The common operator flow: "this expired yesterday, give it
        another day".  ``now + 24h`` must produce a future timestamp
        even when the existing ``expires_at`` is far in the past;
        ``expires_at + 24h`` would still leave the invite expired."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        # Backdate to a week ago
        from datetime import datetime, timezone
        await db._db.execute(
            "UPDATE invites SET expires_at = '2020-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (invite.id,),
        )
        await db._db.commit()

        extended = await db.extend_invite(acct.id, invite.id, hours=24)
        assert extended is not None
        # New expiry is in the future, not 2020+24h
        assert datetime.fromisoformat(extended.expires_at) > datetime.now(timezone.utc)
        # Sanity: an extended invite is no longer expired
        assert extended.is_expired is False

    @pytest.mark.asyncio
    async def test_extend_invite_returns_none_for_used(self, seeded_db):
        """Used invites must NOT be extendable — the user already
        joined, so an extend would be a no-op AND would write a
        confusing audit-log entry."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        user = await db.redeem_invite(invite.code, 902_001)
        assert user is not None

        result = await db.extend_invite(acct.id, invite.id, hours=24)
        assert result is None

    @pytest.mark.asyncio
    async def test_extend_invite_returns_none_for_revoked(self, seeded_db):
        """Revoked invites must NOT be extendable — extending would
        silently un-revoke an operator's prior kill decision.  Force
        them to create a new code if they really want to re-onboard."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        await db.revoke_invite(acct.id, invite.id)

        result = await db.extend_invite(acct.id, invite.id, hours=24)
        assert result is None
        # And the row is still marked revoked — extend didn't clobber
        admin_view = await db.get_invite_by_id(acct.id, invite.id)
        assert admin_view is not None
        assert admin_view.is_revoked is True

    @pytest.mark.asyncio
    async def test_extend_invite_returns_none_for_unknown_id(self, seeded_db):
        """Symmetric to revoke's 'unknown id' branch."""
        db, acct, _, _ = seeded_db
        assert await db.extend_invite(acct.id, 99999, hours=24) is None

    @pytest.mark.asyncio
    async def test_extend_invite_is_monotonic_forward(self, seeded_db):
        """Extending by a SHORTER window than what's already left
        must NOT pull the deadline back.  Operator clicks Extend → 1h
        on a 100h-remaining invite → the deadline stays at the
        original (later) value, not now+1h.  Defends against the
        stealth-revoke that anchor-to-now alone would allow."""
        db, acct, _, owner = seeded_db
        from datetime import datetime, timezone
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET, hours=100)
        original_expires = datetime.fromisoformat(invite.expires_at)

        extended = await db.extend_invite(acct.id, invite.id, hours=1)
        assert extended is not None
        new_expires = datetime.fromisoformat(extended.expires_at)
        # The 100h existing window dominates the 1h request — the
        # deadline stayed where it was (might shift by a few seconds
        # depending on test-execution drift, but never EARLIER).
        assert new_expires >= original_expires
        # Specifically, new_expires must NOT be ~1h from now — that
        # would mean we pulled the deadline back by ~99h.
        delta_from_now = (new_expires - datetime.now(timezone.utc)).total_seconds()
        assert delta_from_now > 90 * 3600, (
            "Expected ~100h remaining; got "
            f"{delta_from_now / 3600:.1f}h — anchor-to-now silently "
            "pulled the deadline backwards"
        )

    @pytest.mark.asyncio
    async def test_extend_invite_validates_hours_bound(self, seeded_db):
        """Storage method defends against ``hours < 1`` and
        ``hours > 720`` even when a future caller bypasses the
        Pydantic route guard (CLI, ARQ job, bot wizard).  hours=0
        would silently stealth-revoke the invite; negative hours
        would create a backwards-time deadline.  Both raise."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        with pytest.raises(ValueError, match="hours must be"):
            await db.extend_invite(acct.id, invite.id, hours=0)
        with pytest.raises(ValueError, match="hours must be"):
            await db.extend_invite(acct.id, invite.id, hours=-5)
        with pytest.raises(ValueError, match="hours must be"):
            await db.extend_invite(acct.id, invite.id, hours=10_000)

    # ── email channel (migration 088) ───────────────────────────────

    @pytest.mark.asyncio
    async def test_create_invite_email_channel_stamps_recipient(self, seeded_db):
        """create_invite(recipient_email=...) stamps sent_to_email on
        the row (encrypted at rest when ENCRYPTION_KEY is set) and
        derives channel='email'.  Round-trips cleanly through
        get_invite + get_invite_by_id."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.DRIVER, "ops",
            recipient_email="alice@example.com",
        )
        assert invite.sent_to_email == "alice@example.com"
        assert invite.channel == "email"
        assert invite.email_sent_at is None
        assert invite.email_send_count == 0
        # Round-trip through get_invite (recipient-side surface)
        fetched = await db.get_invite(invite.code)
        assert fetched is not None
        assert fetched.sent_to_email == "alice@example.com"
        assert fetched.channel == "email"
        # And through get_invite_by_id (admin surface)
        admin_view = await db.get_invite_by_id(acct.id, invite.id)
        assert admin_view is not None
        assert admin_view.sent_to_email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_create_invite_link_channel_defaults(self, seeded_db):
        """Default behaviour unchanged: link-channel invites have
        sent_to_email=NULL and channel='link'.  Defends against the
        email-channel migration silently flipping every existing
        invite into the email path."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET)
        assert invite.sent_to_email is None
        assert invite.channel == "link"
        assert invite.email_sent_at is None
        assert invite.email_send_count == 0

    @pytest.mark.asyncio
    async def test_mark_invite_email_sent_stamps_and_increments(self, seeded_db):
        """First successful send stamps email_sent_at and bumps
        email_send_count to 1; a second call (resend) increments to
        2 and refreshes the timestamp."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="bob@example.com",
        )
        first = await db.mark_invite_email_sent(acct.id, invite.id)
        assert first is not None
        assert first.email_sent_at is not None
        assert first.email_send_count == 1
        first_ts = first.email_sent_at

        second = await db.mark_invite_email_sent(acct.id, invite.id)
        assert second is not None
        assert second.email_send_count == 2
        # Timestamp moved forward (or is at least not earlier)
        assert second.email_sent_at >= first_ts

    @pytest.mark.asyncio
    async def test_mark_invite_email_sent_returns_none_for_used(self, seeded_db):
        """Used invites must NOT accept mark-email-sent (the user
        already joined; no resend possible)."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="carol@example.com",
        )
        await db.redeem_invite(invite.code, 920_001)
        result = await db.mark_invite_email_sent(acct.id, invite.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_mark_invite_email_sent_returns_none_for_revoked(self, seeded_db):
        """Revoked invites must NOT accept mark-email-sent —
        symmetric with the used-row guard."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="dave@example.com",
        )
        await db.revoke_invite(acct.id, invite.id)
        result = await db.mark_invite_email_sent(acct.id, invite.id)
        assert result is None

    # ── bounce / complaint (migration 097) ─────────────────────────

    @pytest.mark.asyncio
    async def test_mark_bounced_hard_flips_immediately(self, seeded_db):
        """Hard bounce stamps email_bounced_at + type='hard' on the
        first event — no soft-bounce counter waiting period."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="hard@example.com",
        )
        updated = await db.mark_invite_email_bounced(
            acct.id, invite.id,
            bounce_type="hard",
            reason="550 5.1.1 mailbox unknown",
        )
        assert updated is not None
        assert updated.is_bounced
        assert updated.email_bounce_type == "hard"
        assert updated.email_bounce_reason == "550 5.1.1 mailbox unknown"
        assert updated.email_soft_bounce_count == 0

    @pytest.mark.asyncio
    async def test_mark_bounced_soft_increments_below_threshold(self, seeded_db):
        """First two soft bounces increment the counter without
        flipping the permanent bounce flag — most soft bounces
        self-clear (greylisting, mailbox full) and we don't want
        to render a permanent 'Bounced' badge prematurely."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="soft@example.com",
        )
        for n in (1, 2):
            updated = await db.mark_invite_email_bounced(
                acct.id, invite.id,
                bounce_type="soft", reason="421 try later",
            )
            assert updated is not None
            assert updated.email_soft_bounce_count == n
            assert not updated.is_bounced, f"flipped at count={n}; should wait until 3"
        # Third soft bounce flips the permanent flag
        third = await db.mark_invite_email_bounced(
            acct.id, invite.id,
            bounce_type="soft", reason="421 still failing",
        )
        assert third.email_soft_bounce_count == 3
        assert third.is_bounced
        assert third.email_bounce_type == "soft"

    @pytest.mark.asyncio
    async def test_mark_complained_flags_without_revoking(self, seeded_db):
        """Spam complaint flags the row (email_complained_at) but
        does NOT revoke it — silent destruction confuses operators.
        Operator chooses revoke vs ignore in the dashboard."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="complainer@example.com",
        )
        updated = await db.mark_invite_email_bounced(
            acct.id, invite.id,
            bounce_type="complaint",
            reason="reported as spam",
        )
        assert updated is not None
        assert updated.is_complained
        assert updated.email_bounce_type == "complaint"
        # Crucially: NOT revoked.  Operator gets to decide.
        assert not updated.is_revoked

    @pytest.mark.asyncio
    async def test_clear_soft_bounce_on_delivered(self, seeded_db):
        """email.delivered after a soft bounce clears the bounced
        state (race recovery — Resend doesn't guarantee ordering,
        so a soft bounce followed by a successful retry's delivered
        event must let the row recover).  Hard bounces stay sticky."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="recovered@example.com",
        )
        # Push 3 soft bounces to trip the permanent flag
        for _ in range(3):
            await db.mark_invite_email_bounced(
                acct.id, invite.id,
                bounce_type="soft", reason="421 transient",
            )
        bounced = await db.get_invite_by_id(acct.id, invite.id)
        assert bounced.is_bounced
        # Now delivered arrives — soft state clears
        cleared = await db.clear_invite_soft_bounce(acct.id, invite.id)
        assert cleared is not None
        assert not cleared.is_bounced
        assert cleared.email_soft_bounce_count == 0
        assert cleared.email_bounce_type is None

    @pytest.mark.asyncio
    async def test_delivered_clears_intermediate_soft_count(self, seeded_db):
        """REGRESSION: clear_invite_soft_bounce must reset the counter
        even when bounce_type is NULL (intermediate state with 1-2
        strikes that hadn't crossed the 3-strike threshold yet).
        Pre-fix, the row's count stayed at 2 forever; the next
        failure would promote to 3 and immediately flip 'bounced',
        misleading the operator into thinking the address had bounced
        3 times when really only 1 actually failed after a successful
        delivery in between."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="intermediate@example.com",
        )
        # Two soft strikes — under the threshold, so type stays NULL
        for _ in range(2):
            await db.mark_invite_email_bounced(
                acct.id, invite.id,
                bounce_type="soft", reason="421 transient",
            )
        pre = await db.get_invite_by_id(acct.id, invite.id)
        assert pre.email_soft_bounce_count == 2
        assert pre.email_bounce_type is None  # not yet flipped
        # Delivered arrives — counter resets
        cleared = await db.clear_invite_soft_bounce(acct.id, invite.id)
        assert cleared is not None
        assert cleared.email_soft_bounce_count == 0
        assert cleared.email_bounce_type is None
        assert not cleared.is_bounced

    @pytest.mark.asyncio
    async def test_clear_soft_bounce_does_not_clear_hard(self, seeded_db):
        """Hard bounces stay sticky after a delivered event —
        a hard-then-delivered sequence is a relay anomaly we
        shouldn't paper over."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="hardstuck@example.com",
        )
        await db.mark_invite_email_bounced(
            acct.id, invite.id,
            bounce_type="hard", reason="550 unknown",
        )
        unchanged = await db.clear_invite_soft_bounce(acct.id, invite.id)
        # Returns the row but DID NOT clear (type was hard not soft)
        assert unchanged is not None
        assert unchanged.is_bounced
        assert unchanged.email_bounce_type == "hard"

    @pytest.mark.asyncio
    async def test_mark_bounced_refuses_used_invite(self, seeded_db):
        """A bounce webhook arriving AFTER the recipient successfully
        signed up via a different channel must NOT flip the row to
        bounced — that would mislead the operator into thinking the
        active user's invite failed."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="usedup@example.com",
        )
        await db.redeem_invite(invite.code, 990_001)
        updated = await db.mark_invite_email_bounced(
            acct.id, invite.id,
            bounce_type="hard", reason="too late",
        )
        assert updated is None

    @pytest.mark.asyncio
    async def test_webhook_idempotency_gate(self, db):
        """svix-id idempotency: first INSERT returns True (process me),
        retries with the same id return False (drop fast).  Mirrors
        Stripe's mark_stripe_event_processed pattern."""
        first = await db.mark_email_webhook_event_seen(
            "msg_test_abc", "email.bounced", account_id=1, invite_id=42,
        )
        assert first is True
        # Resend's retry storm replays the same svix_id
        retry = await db.mark_email_webhook_event_seen(
            "msg_test_abc", "email.bounced", account_id=1, invite_id=42,
        )
        assert retry is False

    @pytest.mark.asyncio
    async def test_find_by_resend_email_id(self, seeded_db):
        """Webhook handler's primary invite lookup path."""
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(
            acct.id, owner.id, Role.FLEET,
            recipient_email="lookup@example.com",
        )
        await db.set_invite_resend_email_id(acct.id, invite.id, "re_abc123")
        found = await db.find_invite_by_resend_email_id("re_abc123")
        assert found is not None
        assert found.id == invite.id
        # Unknown id returns None — webhook handler treats as no-op
        assert await db.find_invite_by_resend_email_id("re_unknown") is None
        # Empty string short-circuits to None (defends against bad
        # webhook payloads sending email_id="")
        assert await db.find_invite_by_resend_email_id("") is None

    @pytest.mark.asyncio
    async def test_create_invite_recipient_email_encrypted_at_rest(self, seeded_db):
        """recipient_email lands ENCRYPTED in the invites.sent_to_email
        column when ENCRYPTION_KEY is set, and round-trips back to
        plaintext via _row_to_invite.  Defends the "PII encrypted at
        rest" claim that the schema migration docstring makes."""
        import infra.crypto as encryption
        from unittest.mock import patch as _patch
        db, acct, _, owner = seeded_db
        encryption._fernet = None
        with _patch.dict(os.environ, {"ENCRYPTION_KEY": "invite-test-key"}):
            encryption.init_encryption()
            invite = await db.create_invite(
                acct.id, owner.id, Role.FLEET,
                recipient_email="frank@example.com",
            )
            # Returned dataclass holds PLAINTEXT (callers shouldn't
            # see ciphertext)
            assert invite.sent_to_email == "frank@example.com"
            # But the raw row in the DB has ciphertext.  Read it
            # directly via the storage layer's internal connection so
            # we bypass _row_to_invite's decrypt.
            cur = await db._db.execute(
                "SELECT sent_to_email FROM invites WHERE id = ?",
                (invite.id,),
            )
            row = await cur.fetchone()
            stored = row[0]
            assert stored is not None
            assert stored != "frank@example.com", (
                "sent_to_email should be encrypted at rest, got plaintext"
            )
            assert stored.startswith("enc::"), (
                "infra.crypto.encrypt prefixes 'enc::'; got %r" % stored
            )
            # Hydration via get_invite_by_id decrypts back to plaintext.
            hydrated = await db.get_invite_by_id(acct.id, invite.id)
            assert hydrated is not None
            assert hydrated.sent_to_email == "frank@example.com"
        encryption._fernet = None

    @pytest.mark.asyncio
    async def test_mark_invite_email_sent_rejects_cross_account(self, db):
        """Tenant isolation symmetric with revoke/extend tests."""
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        owner_a = await db.create_user(
            telegram_id=921_001, account_id=acct_a.id, role=Role.OWNER,
        )
        invite = await db.create_invite(
            acct_a.id, owner_a.id, Role.FLEET,
            recipient_email="eve@example.com",
        )
        assert await db.mark_invite_email_sent(acct_b.id, invite.id) is None
        ok = await db.mark_invite_email_sent(acct_a.id, invite.id)
        assert ok is not None

    @pytest.mark.asyncio
    async def test_extend_invite_rejects_cross_account(self, db):
        """Tenant isolation: account B cannot extend account A's invite
        by guessing a numeric id.  Mirrors revoke's cross-account test
        — both the SELECT and the UPDATE carry account_id as defense-
        in-depth against an RLS rollback."""
        acct_a = await db.create_account("A Co")
        acct_b = await db.create_account("B Co")
        owner_a = await db.create_user(
            telegram_id=910_001, account_id=acct_a.id, role=Role.OWNER,
        )
        invite = await db.create_invite(acct_a.id, owner_a.id, Role.FLEET)
        # Account B tries to extend it
        assert await db.extend_invite(acct_b.id, invite.id, hours=24) is None
        # Account A succeeds — proves the row exists, B was the
        # thing the storage layer rejected
        extended = await db.extend_invite(acct_a.id, invite.id, hours=24)
        assert extended is not None


# ══════════════════════════════════════════════════════════════════
# MULTI-TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════

class TestMultiTenantIsolation:

    @pytest.mark.asyncio
    async def test_companies_isolated_between_accounts(self, db):
        a1 = await db.create_account("Fleet A")
        a2 = await db.create_account("Fleet B")
        await db.add_company(a1.id, "CO1", "key_a", "Company A")
        await db.add_company(a2.id, "CO1", "key_b", "Company B")

        cos_a = await db.get_account_companies(a1.id)
        cos_b = await db.get_account_companies(a2.id)
        assert len(cos_a) == 1
        assert len(cos_b) == 1
        assert cos_a[0].samsara_api_key != cos_b[0].samsara_api_key

    @pytest.mark.asyncio
    async def test_users_isolated_between_accounts(self, db):
        a1 = await db.create_account("Fleet A")
        a2 = await db.create_account("Fleet B")
        await db.create_user(111, a1.id, Role.OWNER)
        await db.create_user(222, a2.id, Role.OWNER)

        users_a = await db.list_account_users(a1.id)
        users_b = await db.list_account_users(a2.id)
        assert len(users_a) == 1
        assert len(users_b) == 1
        assert users_a[0].telegram_id == 111
        assert users_b[0].telegram_id == 222

    @pytest.mark.asyncio
    async def test_invites_bound_to_account(self, db):
        a1 = await db.create_account("Fleet A")
        owner = await db.create_user(111, a1.id, Role.OWNER)
        invite = await db.create_invite(a1.id, owner.id, Role.DRIVER)

        # New user joins via invite — should be in account a1
        user = await db.redeem_invite(invite.code, 999)
        assert user.account_id == a1.id


# ══════════════════════════════════════════════════════════════════
# AUTHORIZED CHATS
# ══════════════════════════════════════════════════════════════════

class TestAuthorizedChats:

    @pytest.mark.asyncio
    async def test_add_and_check_chat(self, seeded_db):
        db, acct, _, owner = seeded_db
        await db.add_authorized_chat(acct.id, -100123, "Fleet Group", owner.id)
        assert await db.is_chat_authorized(-100123) is True

    @pytest.mark.asyncio
    async def test_unauthorized_chat_returns_false(self, db):
        assert await db.is_chat_authorized(-999999) is False

    @pytest.mark.asyncio
    async def test_remove_chat(self, seeded_db):
        db, acct, _, owner = seeded_db
        await db.add_authorized_chat(acct.id, -100123, "Fleet Group", owner.id)
        await db.remove_authorized_chat(acct.id, -100123)
        assert await db.is_chat_authorized(-100123) is False


# ══════════════════════════════════════════════════════════════════
# MAINTENANCE TASKS
# ══════════════════════════════════════════════════════════════════

class TestMaintenanceTasks:

    @pytest.mark.asyncio
    async def test_add_and_list_tasks(self, seeded_db):
        db, acct, _, owner = seeded_db
        task_id = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil_change",
            "Oil change due", due_miles=50000, created_by=owner.id,
        )
        assert task_id > 0
        tasks = await db.get_maintenance_tasks(acct.id)
        assert len(tasks) == 1
        assert tasks[0]["task_type"] == "oil_change"

    @pytest.mark.asyncio
    async def test_update_task_status(self, seeded_db):
        db, acct, _, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "tire",
            "Tire rotation", created_by=owner.id,
        )
        await db.update_maintenance_status(tid, "done")
        tasks = await db.get_maintenance_tasks(acct.id, status="done")
        assert len(tasks) == 1
        assert tasks[0]["completed_at"] is not None

    @pytest.mark.asyncio
    async def test_get_single_task(self, seeded_db):
        db, acct, _, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil",
            "Oil change", created_by=owner.id,
        )
        task = await db.get_maintenance_task(tid)
        assert task is not None
        assert task["task_type"] == "oil"
        assert task["vehicle_name"] == "Truck 101"

    @pytest.mark.asyncio
    async def test_get_single_task_not_found(self, seeded_db):
        db, acct, _, owner = seeded_db
        task = await db.get_maintenance_task(99999)
        assert task is None

    @pytest.mark.asyncio
    async def test_update_maintenance_task_fields(self, seeded_db):
        db, acct, _, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil",
            "Oil change", created_by=owner.id,
        )
        result = await db.update_maintenance_task(
            tid, task_type="brakes", description="Brake job",
            due_date="2026-06-01", due_miles=80000,
        )
        assert result is True
        task = await db.get_maintenance_task(tid)
        assert task["task_type"] == "brakes"
        assert task["description"] == "Brake job"
        assert task["due_date"] == "2026-06-01"
        assert task["due_miles"] == 80000

    @pytest.mark.asyncio
    async def test_update_maintenance_task_ignores_bad_fields(self, seeded_db):
        db, acct, _, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil",
            "Oil change", created_by=owner.id,
        )
        result = await db.update_maintenance_task(tid, fake_field="bad")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_maintenance_task(self, seeded_db):
        db, acct, _, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil",
            "Oil change", created_by=owner.id,
        )
        await db.delete_maintenance_task(tid)
        task = await db.get_maintenance_task(tid)
        assert task is None
        tasks = await db.get_maintenance_tasks(acct.id)
        assert len(tasks) == 0

    @pytest.mark.asyncio
    async def test_add_task_with_recurring(self, seeded_db):
        db, acct, _, owner = seeded_db
        tid = await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil",
            "Oil change", created_by=owner.id,
            recur_interval_days=90, recur_interval_miles=5000,
        )
        task = await db.get_maintenance_task(tid)
        assert task["recur_interval_days"] == 90
        assert task["recur_interval_miles"] == 5000

    @pytest.mark.asyncio
    async def test_get_pending_tasks_by_miles(self, seeded_db):
        db, acct, _, owner = seeded_db
        await db.add_maintenance_task(
            acct.id, "TFC", "Truck 101", "oil",
            "Oil change", due_miles=50000, created_by=owner.id,
        )
        # Task with no due_miles should NOT appear
        await db.add_maintenance_task(
            acct.id, "TFC", "Truck 102", "brakes",
            "Brake check", created_by=owner.id,
        )
        tasks = await db.get_pending_tasks_by_miles(acct.id)
        assert len(tasks) == 1
        assert tasks[0]["vehicle_name"] == "Truck 101"


# ══════════════════════════════════════════════════════════════════
# FUEL ENTRIES
# ══════════════════════════════════════════════════════════════════

class TestFuelEntries:

    @pytest.mark.asyncio
    async def test_add_fuel_entry(self, seeded_db):
        db, acct, _, owner = seeded_db
        fid = await db.add_fuel_entry(
            acct.id, "TFC", "Truck 101",
            gallons=50.0, price_per_gallon=3.50,
            odometer_miles=120000, date="2026-03-15",
            created_by=owner.id,
        )
        assert fid > 0
        entries = await db.get_fuel_entries(acct.id)
        assert len(entries) == 1
        assert entries[0]["total_cost"] == 175.0

    @pytest.mark.asyncio
    async def test_fuel_summary(self, seeded_db):
        db, acct, _, owner = seeded_db
        await db.add_fuel_entry(
            acct.id, "TFC", "Truck 101", 50.0, 3.50, 120000, "2026-03-01",
            created_by=owner.id,
        )
        await db.add_fuel_entry(
            acct.id, "TFC", "Truck 101", 45.0, 3.60, 120500, "2026-03-10",
            created_by=owner.id,
        )
        summary = await db.get_fuel_summary(acct.id)
        assert len(summary) == 1
        assert summary[0]["entries"] == 2
        assert summary[0]["total_gallons"] == 95.0


# ══════════════════════════════════════════════════════════════════
# AUTO REPORTS SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════

class TestAutoReportsSubscriptions:

    @pytest.mark.asyncio
    async def test_subscribe_auto_report(self, seeded_db):
        db, _, _, owner = seeded_db
        await db.subscribe_digest_ext(owner.id, "daily", 7, "UTC", "faults")
        sub = await db.get_digest_subscription(owner.id)
        assert sub is not None
        assert sub["frequency"] == "daily"
        assert sub["send_hour"] == 7
        assert sub["report_type"] == "faults"

    @pytest.mark.asyncio
    async def test_unsubscribe_auto_report(self, seeded_db):
        db, _, _, owner = seeded_db
        await db.subscribe_digest_ext(owner.id, "weekly", 9, "UTC", "fuel")
        await db.unsubscribe_digest(owner.id)
        sub = await db.get_digest_subscription(owner.id)
        assert sub is None

    @pytest.mark.asyncio
    async def test_resubscribe_updates(self, seeded_db):
        # subscribe_digest_ext upserts on (user_id, report_type) since
        # the 2026-06 multi-schedule model — a user can stack Faults
        # Daily + Fuel Weekly + Health Monthly as three independent
        # rows.  So "resubscribe" semantics only update when the same
        # report_type is replayed; a different report_type would create
        # a second row, not overwrite the first.  This test guards the
        # same-key update path.
        db, _, _, owner = seeded_db
        await db.subscribe_digest_ext(owner.id, "daily", 7, "UTC", "faults")
        await db.subscribe_digest_ext(owner.id, "weekly", 12, "America/Chicago", "faults")
        sub = await db.get_digest_subscription(owner.id)
        assert sub["frequency"] == "weekly"
        assert sub["send_hour"] == 12
        assert sub["report_type"] == "faults"


# ══════════════════════════════════════════════════════════════════
# ENCRYPTION MIGRATION
# ══════════════════════════════════════════════════════════════════

class TestEncryptionMigration:

    @pytest.mark.asyncio
    async def test_migrate_encrypts_plaintext_keys(self, db):
        """When encryption is enabled, migration encrypts existing plaintext keys."""
        import infra.crypto as encryption
        from unittest.mock import patch as _patch

        acct = await db.create_account("Enc Test")

        # Disable encryption, add company with plaintext key
        encryption._fernet = None
        with _patch.dict(os.environ, {"ENCRYPTION_KEY": ""}):
            encryption.init_encryption()
        await db.add_company(acct.id, "PT1", "samsara_api_plaintext_key", "Plain Co")

        # Now enable encryption
        with _patch.dict(os.environ, {"ENCRYPTION_KEY": "migration-test-key"}):
            encryption.init_encryption()

        count = await db.migrate_encrypt_api_keys()
        assert count == 1

        # Verify the key decrypts correctly
        companies = await db.get_account_companies(acct.id)
        assert companies[0].samsara_api_key == "samsara_api_plaintext_key"

        # Clean up
        encryption._fernet = None

    @pytest.mark.asyncio
    async def test_migrate_skips_already_encrypted(self, db):
        """Already-encrypted keys are not re-encrypted."""
        import infra.crypto as encryption
        from unittest.mock import patch as _patch

        with _patch.dict(os.environ, {"ENCRYPTION_KEY": "test-key"}):
            encryption.init_encryption()

        acct = await db.create_account("Enc Test 2")
        await db.add_company(acct.id, "E1", "samsara_api_key1")

        # First migration
        count1 = await db.migrate_encrypt_api_keys()
        assert count1 == 0  # Already encrypted on insert

        # Second call — should skip
        count2 = await db.migrate_encrypt_api_keys()
        assert count2 == 0

        encryption._fernet = None

    @pytest.mark.asyncio
    async def test_migrate_disabled_returns_zero(self, db):
        """Migration does nothing when encryption is disabled."""
        import infra.crypto as encryption
        from unittest.mock import patch as _patch

        with _patch.dict(os.environ, {"ENCRYPTION_KEY": ""}):
            encryption.init_encryption()

        count = await db.migrate_encrypt_api_keys()
        assert count == 0
