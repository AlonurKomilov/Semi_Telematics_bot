"""Tests for the database layer — async SQLite operations.

Uses a temporary in-memory database for isolation.
"""

import os
import pytest
import pytest_asyncio
from unittest.mock import patch

# Ensure encryption is not active during DB tests (unless explicitly tested)
os.environ.setdefault("ENCRYPTION_KEY", "")

from database import Database, Role, Account, Company, User, Invite


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
    async def test_slug_uniqueness(self, db):
        a1 = await db.create_account("My Fleet")
        a2 = await db.create_account("My Fleet")
        assert a1.slug != a2.slug  # hash suffix prevents collision


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
        await db.remove_company(co.id)
        active = await db.get_account_companies(acct.id, active_only=True)
        assert len(active) == 0
        # Still in DB with active_only=False
        all_cos = await db.get_account_companies(acct.id, active_only=False)
        assert len(all_cos) == 1

    @pytest.mark.asyncio
    async def test_update_company(self, seeded_db):
        db, _, co, _ = seeded_db
        await db.update_company(co.id, display_name="New Name")
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
        fleet = await db.create_user(222222, acct.id, Role.FLEET_MGR)
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
            acct.id, owner.id, Role.FLEET_MGR, "ops"
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
            acct.id, owner.id, Role.DISPATCHER, "dispatch"
        )
        new_user = await db.redeem_invite(invite.code, 555555)
        assert new_user is not None
        assert new_user.role == Role.DISPATCHER
        assert new_user.department == "dispatch"
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
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET_MGR)
        await db.redeem_invite(invite.code, 555555)
        result = await db.redeem_invite(invite.code, 666666)
        assert result is None  # already used

    @pytest.mark.asyncio
    async def test_cannot_redeem_expired(self, seeded_db):
        db, acct, _, owner = seeded_db
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET_MGR, hours=0)
        # hours=0 means expires immediately
        import asyncio
        await asyncio.sleep(0.01)
        result = await db.redeem_invite(invite.code, 555555)
        # Depending on timing, this may or may not be expired.
        # Create truly expired invite by manipulating DB directly
        inv2 = await db.create_invite(acct.id, owner.id, Role.FLEET_MGR)
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
        invite = await db.create_invite(acct.id, owner.id, Role.FLEET_MGR)
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
        tasks = await db.get_pending_tasks_by_miles()
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
        db, _, _, owner = seeded_db
        await db.subscribe_digest_ext(owner.id, "daily", 7, "UTC", "faults")
        await db.subscribe_digest_ext(owner.id, "weekly", 12, "America/Chicago", "health")
        sub = await db.get_digest_subscription(owner.id)
        assert sub["frequency"] == "weekly"
        assert sub["send_hour"] == 12
        assert sub["report_type"] == "health"


# ══════════════════════════════════════════════════════════════════
# ENCRYPTION MIGRATION
# ══════════════════════════════════════════════════════════════════

class TestEncryptionMigration:

    @pytest.mark.asyncio
    async def test_migrate_encrypts_plaintext_keys(self, db):
        """When encryption is enabled, migration encrypts existing plaintext keys."""
        import encryption
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
        import encryption
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
        import encryption
        from unittest.mock import patch as _patch

        with _patch.dict(os.environ, {"ENCRYPTION_KEY": ""}):
            encryption.init_encryption()

        count = await db.migrate_encrypt_api_keys()
        assert count == 0
