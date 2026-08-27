"""Storage mixins behind the alerting and scheduling surfaces.

Every class here drives db.* directly — quiet hours, alert acks, the
audit log, digest subscriptions, AI usage rows, work hours, maintenance
suppression, alert history, parking events. That makes them
adapters/storage tests, which stay in tests/ by design: storage is a
LAYER, not a feature.

Split from tests/test_new_features.py — 139 tests, 23 classes, whose
docstring listed seven unrelated subjects and then grew four more on
top. "New features" named WHEN they arrived, not what they are, which
is how one file came to hold four owners.
"""

import os
import pytest
import pytest_asyncio

os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.storage import Database, Role, User


@pytest_asyncio.fixture
async def seeded(db: Database):
    account = await db.create_account("Test Fleet")
    owner = await db.create_user(telegram_id=100001, account_id=account.id, role=Role.OWNER)
    driver = await db.create_user(telegram_id=100002, account_id=account.id, role=Role.DRIVER, truck_num="101")
    return {"db": db, "account": account, "owner": owner, "driver": driver}


class TestQuietHours:
    async def test_default_no_quiet_hours(self, seeded):
        user = seeded["owner"]
        assert user.quiet_start is None
        assert user.quiet_end is None

    async def test_set_quiet_hours(self, seeded):
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, quiet_start=22, quiet_end=6)
        updated = await db.get_user(user.id)
        assert updated.quiet_start == 22
        assert updated.quiet_end == 6

    async def test_is_in_quiet_hours_during(self, seeded):
        from unittest.mock import patch
        from datetime import datetime as real_dt, timezone as tz
        db, user = seeded["db"], seeded["owner"]
        # Working hours 6-22 means alerts queue OUTSIDE 6-22
        await db.update_user(user.id, quiet_start=6, quiet_end=22, timezone="UTC")
        updated = await db.get_user(user.id)
        # Mock datetime.now so that the local hour is 23 (OUTSIDE working hours)
        fake_now = real_dt(2025, 6, 15, 23, 0, 0, tzinfo=tz.utc)
        original_dt = real_dt

        class FakeDatetime(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now.astimezone(tz_val) if tz_val else fake_now

        with patch("adapters.storage.models.datetime", FakeDatetime):
            assert updated.is_in_quiet_hours() is True

    async def test_is_in_quiet_hours_outside(self, seeded):
        from unittest.mock import patch
        from datetime import datetime as real_dt, timezone as tz
        db, user = seeded["db"], seeded["owner"]
        # Working hours 6-22 means hour 12 is INSIDE working hours
        await db.update_user(user.id, quiet_start=6, quiet_end=22, timezone="UTC")
        updated = await db.get_user(user.id)
        # Mock current time to 12:00 UTC (INSIDE working hours)
        fake_now = real_dt(2025, 6, 15, 12, 0, 0, tzinfo=tz.utc)

        class FakeDatetime(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now.astimezone(tz_val) if tz_val else fake_now

        with patch("adapters.storage.models.datetime", FakeDatetime):
            assert updated.is_in_quiet_hours() is False

    async def test_is_in_quiet_hours_none(self, seeded):
        user = seeded["owner"]
        assert user.is_in_quiet_hours() is False

    async def test_disable_quiet_hours(self, seeded):
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, quiet_start=22, quiet_end=6)
        await db.update_user(user.id, quiet_start=None, quiet_end=None)
        updated = await db.get_user(user.id)
        assert updated.quiet_start is None
        assert updated.quiet_end is None

    async def test_working_hours_midnight_to_8am(self, seeded):
        """Working hours 00:00-08:00: alerts at 9 AM should be queued."""
        from unittest.mock import patch
        from datetime import datetime as real_dt, timezone as tz
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, quiet_start=0, quiet_end=8, timezone="UTC")
        updated = await db.get_user(user.id)

        # 9 AM UTC — OUTSIDE working hours (00:00-08:00) → should queue
        fake_now = real_dt(2025, 6, 15, 9, 0, 0, tzinfo=tz.utc)
        class FakeDatetime9(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now.astimezone(tz_val) if tz_val else fake_now
        with patch("adapters.storage.models.datetime", FakeDatetime9):
            assert updated.is_in_quiet_hours() is True  # outside working hours

        # 3 AM UTC — INSIDE working hours (00:00-08:00) → should deliver
        fake_now2 = real_dt(2025, 6, 15, 3, 0, 0, tzinfo=tz.utc)
        class FakeDatetime3(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now2.astimezone(tz_val) if tz_val else fake_now2
        with patch("adapters.storage.models.datetime", FakeDatetime3):
            assert updated.is_in_quiet_hours() is False  # inside working hours

    async def test_working_hours_boundary_exact(self, seeded):
        """At exactly the end hour, user should be outside working hours."""
        from unittest.mock import patch
        from datetime import datetime as real_dt, timezone as tz
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, quiet_start=0, quiet_end=8, timezone="UTC")
        updated = await db.get_user(user.id)

        # Exactly 8:00 AM → the end boundary, so NOT in working hours
        fake_now = real_dt(2025, 6, 15, 8, 0, 0, tzinfo=tz.utc)
        class FakeDatetime(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now.astimezone(tz_val) if tz_val else fake_now
        with patch("adapters.storage.models.datetime", FakeDatetime):
            assert updated.is_in_quiet_hours() is True  # outside working hours


class TestUserTimezone:
    async def test_default_timezone(self, seeded):
        user = seeded["owner"]
        assert user.timezone == "America/New_York"

    async def test_set_timezone(self, seeded):
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, timezone="America/Chicago")
        updated = await db.get_user(user.id)
        assert updated.timezone == "America/Chicago"


class TestAlertAcknowledgments:
    async def _make_ack(self, db, account_id, vehicle="Truck 101"):
        return await db.create_alert_ack(
            account_id=account_id,
            alert_type="fault",
            vehicle_id="v1",
            vehicle_name=vehicle,
            alert_key="fault_v1_12345",
            message_id=1001,
            chat_id=2001,
            sent_to=100001,
        )

    async def test_create_alert_ack(self, seeded):
        db, account = seeded["db"], seeded["account"]
        ack_id = await self._make_ack(db, account.id)
        assert isinstance(ack_id, int)
        assert ack_id > 0

    async def test_acknowledge_alert(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        ack_id = await self._make_ack(db, account.id)
        result = await db.acknowledge_alert(ack_id, owner.telegram_id)
        assert result is True


class TestAuditLog:
    async def test_add_audit_entry(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.add_audit_log(
            account_id=account.id,
            user_id=owner.id,
            action="test_action",
            target_type="user",
            target_id=str(owner.id),
            details="Did a thing",
        )
        entries = await db.get_audit_log(account.id)
        assert len(entries) == 1
        assert entries[0]["action"] == "test_action"
        assert entries[0]["details"] == "Did a thing"

    async def test_audit_log_ordering(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.add_audit_log(account.id, owner.id, "first")
        await db.add_audit_log(account.id, owner.id, "second")
        entries = await db.get_audit_log(account.id)
        # Most recent first
        assert entries[0]["action"] == "second"
        assert entries[1]["action"] == "first"

    async def test_audit_log_limit(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        for i in range(10):
            await db.add_audit_log(account.id, owner.id, f"action_{i}")
        entries = await db.get_audit_log(account.id, limit=5)
        assert len(entries) == 5


class TestAutoReportsSubscription:
    async def test_subscribe_with_timezone_and_type(self, seeded):
        db, owner = seeded["db"], seeded["owner"]
        await db.subscribe_digest_ext(
            user_id=owner.id,
            frequency="daily",
            send_hour=9,
            timezone="America/Chicago",
            report_type="faults",
        )
        sub = await db.get_digest_subscription(owner.id)
        assert sub is not None
        assert sub["frequency"] == "daily"
        assert sub["send_hour"] == 9
        assert sub["timezone"] == "America/Chicago"
        assert sub["report_type"] == "faults"

    async def test_get_digest_subscribers_by_local_hour(self, seeded):
        db, owner, driver = seeded["db"], seeded["owner"], seeded["driver"]
        await db.subscribe_digest_ext(owner.id, "daily", send_hour=7, timezone="UTC", report_type="faults")
        await db.subscribe_digest_ext(driver.id, "daily", send_hour=9, timezone="UTC", report_type="fuel")
        subs_7 = await db.get_digest_subscribers_by_local_hour(7)
        subs_9 = await db.get_digest_subscribers_by_local_hour(9)
        assert len(subs_7) == 1
        assert len(subs_9) == 1


class TestAIUsageTracking:
    """Tests for AI usage logging and stats retrieval."""

    @pytest.mark.asyncio
    async def test_log_ai_usage(self, seeded_db):
        db = seeded_db["db"]
        acct = seeded_db["account"]
        owner = seeded_db["owner"]
        row_id = await db.log_ai_usage(
            account_id=acct.id, user_id=owner.telegram_id,
            model="gemini-2.0-flash", request_type="chat",
            prompt_tokens=100, reply_tokens=50, total_tokens=150,
        )
        assert isinstance(row_id, int) and row_id > 0

    @pytest.mark.asyncio
    async def test_get_ai_usage_stats_single(self, seeded_db):
        db = seeded_db["db"]
        acct = seeded_db["account"]
        owner = seeded_db["owner"]
        await db.log_ai_usage(
            account_id=acct.id, user_id=owner.telegram_id,
            model="gemini-2.0-flash", request_type="chat",
            prompt_tokens=100, reply_tokens=50, total_tokens=150,
        )
        stats = await db.get_ai_usage_stats(acct.id, days=30)
        assert stats["total_requests"] == 1
        assert stats["total_tokens"] == 150
        assert "chat" in stats["by_type"]
        assert stats["by_type"]["chat"]["requests"] == 1
        assert "gemini-2.0-flash" in stats["by_model"]

    @pytest.mark.asyncio
    async def test_get_ai_usage_stats_multiple_types(self, seeded_db):
        db = seeded_db["db"]
        acct = seeded_db["account"]
        uid = seeded_db["owner"].telegram_id
        await db.log_ai_usage(acct.id, uid, "gemini-2.0-flash", "chat", 10, 5, 15)
        await db.log_ai_usage(acct.id, uid, "gemini-2.0-flash", "summary", 200, 100, 300)
        await db.log_ai_usage(acct.id, uid, "gemini-2.0-flash", "diagnosis", 50, 30, 80)
        stats = await db.get_ai_usage_stats(acct.id, days=30)
        assert stats["total_requests"] == 3
        assert stats["total_tokens"] == 395
        assert len(stats["by_type"]) == 3

    @pytest.mark.asyncio
    async def test_get_ai_usage_stats_empty(self, seeded_db):
        db = seeded_db["db"]
        acct = seeded_db["account"]
        stats = await db.get_ai_usage_stats(acct.id, days=30)
        assert stats["total_requests"] == 0
        assert stats["total_tokens"] == 0
        assert stats["by_type"] == {}
        assert stats["by_model"] == {}
        assert stats["days"] == 30

    @pytest.mark.asyncio
    async def test_get_ai_usage_daily(self, seeded_db):
        db = seeded_db["db"]
        acct = seeded_db["account"]
        uid = seeded_db["owner"].telegram_id
        await db.log_ai_usage(acct.id, uid, "gemini-2.0-flash", "chat", 10, 5, 15)
        await db.log_ai_usage(acct.id, uid, "gemini-2.0-flash", "chat", 20, 10, 30)
        daily = await db.get_ai_usage_daily(acct.id, days=7)
        assert len(daily) >= 1
        day_row = daily[0]
        assert "day" in day_row
        assert day_row["requests"] == 2
        assert day_row["tokens"] == 45

    @pytest.mark.asyncio
    async def test_usage_isolation_between_accounts(self, seeded_db):
        db = seeded_db["db"]
        acct = seeded_db["account"]
        uid = seeded_db["owner"].telegram_id
        await db.log_ai_usage(acct.id, uid, "gemini-2.0-flash", "chat", 10, 5, 15)
        # Stats for a different account should be empty
        stats = await db.get_ai_usage_stats(acct.id + 999, days=30)
        assert stats["total_requests"] == 0


class TestSharedAcknowledgment:
    """Tests for shared ack — acking one alert acks all with same key."""

    async def test_shared_ack_same_key(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        # Create two alerts with the same alert_key (sent to different users)
        ack_id1 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="shared_key_1",
            message_id=1, chat_id=1001, sent_to=1001,
        )
        ack_id2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="shared_key_1",
            message_id=2, chat_id=2002, sent_to=2002,
        )
        # Ack the first one
        await db.acknowledge_alert(ack_id1, owner.telegram_id)
        # Both should now be acked
        pending = await db.get_pending_alerts(account.id)
        assert len(pending) == 0

    async def test_different_keys_not_shared(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        ack_id1 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="key_A",
            message_id=1, chat_id=1001, sent_to=1001,
        )
        ack_id2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v2", vehicle_name="T200",
            alert_key="key_B",
            message_id=2, chat_id=2002, sent_to=2002,
        )
        await db.acknowledge_alert(ack_id1, owner.telegram_id)
        pending = await db.get_pending_alerts(account.id)
        assert len(pending) == 1
        assert pending[0]["alert_key"] == "key_B"


class TestWorkSchedules:
    """Tests for admin-defined working hour presets."""

    async def test_create_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_hour(
            account_id=account.id, label="Day Shift",
            start_hour=6, end_hour=18, created_by=owner.id,
        )
        assert sched["label"] == "Day Shift"
        assert sched["start_hour"] == 6
        assert sched["end_hour"] == 18

    async def test_list_work_hours(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_hour(account.id, "Day Shift", 6, 18, owner.id)
        await db.create_work_hour(account.id, "Night Shift", 18, 6, owner.id)
        schedules = await db.get_work_hours(account.id)
        assert len(schedules) == 2

    async def test_get_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        created = await db.create_work_hour(account.id, "Office Hours", 8, 17, owner.id)
        fetched = await db.get_work_hour(created["id"])
        assert fetched is not None
        assert fetched["label"] == "Office Hours"

    async def test_update_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        created = await db.create_work_hour(account.id, "Early", 5, 14, owner.id)
        await db.update_work_hour(created["id"], label="Early Bird", start_hour=4, end_hour=13)
        updated = await db.get_work_hour(created["id"])
        assert updated["label"] == "Early Bird"
        assert updated["start_hour"] == 4
        assert updated["end_hour"] == 13

    async def test_delete_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        created = await db.create_work_hour(account.id, "Temp", 9, 17, owner.id)
        await db.delete_work_hour(created["id"])
        result = await db.get_work_hour(created["id"])
        assert result is None

    async def test_schedules_isolation_between_accounts(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_hour(account.id, "Shift A", 6, 18, owner.id)
        # Different account should see nothing
        other_schedules = await db.get_work_hours(999)
        assert len(other_schedules) == 0

    async def test_create_schedule_with_target_role(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_hour(
            account_id=account.id, label="Driver Shift",
            start_hour=7, end_hour=19, created_by=owner.id,
            target_role="driver",
        )
        assert sched["target_role"] == "driver"
        fetched = await db.get_work_hour(sched["id"])
        assert fetched["target_role"] == "driver"

    async def test_default_target_role_is_all(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_hour(account.id, "General", 8, 17, owner.id)
        assert sched["target_role"] == "all"

    async def test_get_work_hours_for_role_all(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_hour(account.id, "All Shift", 6, 18, owner.id, target_role="all")
        await db.create_work_hour(account.id, "Driver Only", 7, 19, owner.id, target_role="driver")
        # Driver should see both 'all' and 'driver'
        driver_schedules = await db.get_work_hours_for_role(account.id, "driver")
        assert len(driver_schedules) == 2

    async def test_get_work_hours_for_role_filtered(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_hour(account.id, "Driver Only", 7, 19, owner.id, target_role="driver")
        await db.create_work_hour(account.id, "Admin Only", 8, 17, owner.id, target_role="admin")
        # Admin should see only 'admin' (not 'driver')
        admin_schedules = await db.get_work_hours_for_role(account.id, "admin")
        assert len(admin_schedules) == 1
        assert admin_schedules[0]["target_role"] == "admin"

    async def test_update_schedule_target_role(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_hour(account.id, "Shift", 6, 18, owner.id)
        await db.update_work_hour(sched["id"], target_role="fleet")
        updated = await db.get_work_hour(sched["id"])
        assert updated["target_role"] == "fleet"

    async def test_get_shift_handoff_data(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        handoff = await db.get_shift_handoff_data(account.id, owner.telegram_id)
        assert "pending_alerts" in handoff
        assert "resolved_alerts" in handoff
        assert "pending_maintenance" in handoff
        assert isinstance(handoff["pending_alerts"], list)

    async def test_shift_handoff_includes_pending_alerts(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="handoff_test",
            message_id=1, chat_id=owner.telegram_id, sent_to=owner.telegram_id,
        )
        handoff = await db.get_shift_handoff_data(account.id, owner.telegram_id)
        assert len(handoff["pending_alerts"]) == 1


class TestMaintenanceSuppression:
    """Tests for suppressing alerts on vehicles in active maintenance."""

    async def test_vehicle_not_in_maintenance(self, seeded):
        db, account = seeded["db"], seeded["account"]
        result = await db.is_vehicle_in_maintenance(account.id, "T100")
        assert result is False

    async def test_vehicle_in_maintenance(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.add_maintenance_task(
            account_id=account.id,
            company_code="TFC",
            vehicle_name="T100",
            task_type="oil",
            description="Oil change",
            created_by=0,
        )
        result = await db.is_vehicle_in_maintenance(account.id, "T100")
        assert result is True

    async def test_completed_maintenance_not_suppressed(self, seeded):
        db, account = seeded["db"], seeded["account"]
        task_id = await db.add_maintenance_task(
            account_id=account.id,
            company_code="TFC",
            vehicle_name="T200",
            task_type="brakes",
            description="Brake job",
            created_by=0,
        )
        await db.update_maintenance_status(task_id, "done")
        result = await db.is_vehicle_in_maintenance(account.id, "T200")
        assert result is False


class TestAlertHistoryAndPending:
    """Tests for alert history and pending alerts queries."""

    async def test_get_alert_history(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="hist_1", message_id=1, chat_id=1001, sent_to=1001,
        )
        await db.create_alert_ack(
            account_id=account.id, alert_type="health",
            vehicle_id="v2", vehicle_name="T200",
            alert_key="hist_2", message_id=2, chat_id=1001, sent_to=1001,
        )
        history = await db.get_alert_history(account.id)
        assert len(history) == 2

    async def test_get_pending_alerts(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="pend_1", message_id=1, chat_id=1001, sent_to=1001,
        )
        ack_id2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v2", vehicle_name="T200",
            alert_key="pend_2", message_id=2, chat_id=1001, sent_to=1001,
        )
        # Ack one
        await db.acknowledge_alert(ack_id2, owner.telegram_id)
        pending = await db.get_pending_alerts(account.id)
        assert len(pending) == 1
        assert pending[0]["vehicle_name"] == "T100"

    async def test_alert_history_limit(self, seeded):
        db, account = seeded["db"], seeded["account"]
        for i in range(10):
            await db.create_alert_ack(
                account_id=account.id, alert_type="fault",
                vehicle_id=f"v{i}", vehicle_name=f"T{i}",
                alert_key=f"lim_{i}", message_id=i, chat_id=1001, sent_to=1001,
            )
        history = await db.get_alert_history(account.id, limit=5)
        assert len(history) == 5


class TestParkingEventsDB:
    """Tests for parking events database CRUD."""

    async def test_create_parking_event(self, seeded):
        db, account = seeded["db"], seeded["account"]
        event = await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="I-95 Highway Shoulder",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=3.5, location_class="unsafe",
        )
        assert event["vehicle_name"] == "T100"
        assert event["location_class"] == "unsafe"
        assert event["duration_hours"] == 3.5
        assert event["resolved"] == 0

    async def test_upsert_updates_existing(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="I-95 Highway Shoulder",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=2.0, location_class="unsafe",
        )
        # Second call updates
        updated = await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="I-95 Highway Shoulder",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=5.0, location_class="unsafe",
        )
        assert updated["duration_hours"] == 5.0

    async def test_resolve_parking_event(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="I-95 Highway Shoulder",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=3.0, location_class="unsafe",
        )
        await db.resolve_parking_event(account.id, "v1")
        active = await db.get_active_parking_event(account.id, "v1")
        assert active is None

    async def test_get_active_parking_events(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="I-95 Shoulder",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=5.0, location_class="unsafe",
        )
        await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v2",
            vehicle_name="T200", company_code="TFC",
            latitude=41.0, longitude=-73.0,
            address="Pilot Travel Center",
            first_stopped="2025-06-15T12:00:00",
            duration_hours=1.0, location_class="safe",
        )
        # attention_only (default) — should only return unsafe/unknown
        events = await db.get_active_parking_events(account.id)
        assert len(events) == 1
        assert events[0]["vehicle_name"] == "T100"

        # attention_only=False — should return all stopped vehicles
        all_events = await db.get_active_parking_events(account.id, attention_only=False)
        assert len(all_events) == 2
        # Sorted by duration_hours DESC
        assert all_events[0]["vehicle_name"] == "T100"

    async def test_update_parking_alert_level(self, seeded):
        db, account = seeded["db"], seeded["account"]
        event = await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="Unknown Location",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=3.0, location_class="unknown",
        )
        await db.update_parking_alert_level(
            event["id"], "warning", "AI says this looks like a rest area."
        )
        updated = await db.get_active_parking_event(account.id, "v1")
        assert updated["alert_level"] == "warning"
        assert "AI says" in updated["ai_analysis"]

    async def test_parking_history(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.upsert_parking_event(
            account_id=account.id, vehicle_id="v1",
            vehicle_name="T100", company_code="TFC",
            latitude=40.7128, longitude=-74.0060,
            address="Highway Shoulder",
            first_stopped="2025-06-15T10:00:00",
            duration_hours=5.0, location_class="unsafe",
        )
        await db.resolve_parking_event(account.id, "v1")
        history = await db.get_parking_history(account.id)
        assert len(history) == 1
        assert history[0]["resolved"] == 1

    async def test_resolve_nonexistent_event(self, seeded):
        db, account = seeded["db"], seeded["account"]
        # Should not raise
        result = await db.resolve_parking_event(account.id, "nonexistent_v")
        assert result is True
