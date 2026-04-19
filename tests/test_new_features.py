"""Tests for new automation features: quiet hours, alert acks, audit log,
auto reports, settings keyboards, onboarding keyboard, rate limiting."""

import os
import pytest
import pytest_asyncio

os.environ.setdefault("ENCRYPTION_KEY", "")

from database import Database, Role, User


# ── Fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded(db: Database):
    account = await db.create_account("Test Fleet")
    owner = await db.create_user(telegram_id=100001, account_id=account.id, role=Role.OWNER)
    driver = await db.create_user(telegram_id=100002, account_id=account.id, role=Role.DRIVER, truck_num="101")
    return {"db": db, "account": account, "owner": owner, "driver": driver}


# ══════════════════════════════════════════════════════════════════
# QUIET HOURS
# ══════════════════════════════════════════════════════════════════

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
        from unittest.mock import patch, MagicMock
        from datetime import datetime as real_dt, timezone as tz
        from zoneinfo import ZoneInfo
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

        with patch("database.models.datetime", FakeDatetime):
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

        with patch("database.models.datetime", FakeDatetime):
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
        with patch("database.models.datetime", FakeDatetime9):
            assert updated.is_in_quiet_hours() is True  # outside working hours

        # 3 AM UTC — INSIDE working hours (00:00-08:00) → should deliver
        fake_now2 = real_dt(2025, 6, 15, 3, 0, 0, tzinfo=tz.utc)
        class FakeDatetime3(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now2.astimezone(tz_val) if tz_val else fake_now2
        with patch("database.models.datetime", FakeDatetime3):
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
        with patch("database.models.datetime", FakeDatetime):
            assert updated.is_in_quiet_hours() is True  # outside working hours


# ══════════════════════════════════════════════════════════════════
# TIMEZONE
# ══════════════════════════════════════════════════════════════════

class TestUserTimezone:
    async def test_default_timezone(self, seeded):
        user = seeded["owner"]
        assert user.timezone == "America/New_York"

    async def test_set_timezone(self, seeded):
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, timezone="America/Chicago")
        updated = await db.get_user(user.id)
        assert updated.timezone == "America/Chicago"


# ══════════════════════════════════════════════════════════════════
# ALERT ACKNOWLEDGMENTS
# ══════════════════════════════════════════════════════════════════

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
            next_escalation="2099-01-01T00:00:00",
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

    async def test_get_unacked_alerts(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await self._make_ack(db, account.id, vehicle="T1")
        await self._make_ack(db, account.id, vehicle="T2")
        unacked = await db.get_unacked_alerts()
        assert len(unacked) == 2

    async def test_acked_not_in_unacked(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        ack_id = await self._make_ack(db, account.id)
        await db.acknowledge_alert(ack_id, owner.telegram_id)
        unacked = await db.get_unacked_alerts()
        assert len(unacked) == 0


# ══════════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# AUTO REPORTS SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════

class TestNewKeyboards:
    def _callbacks(self, kb):
        return [btn.callback_data for row in kb.inline_keyboard for btn in row]

    def test_auto_reports_hour_kb(self):
        from bot.keyboards import auto_reports_hour_kb
        kb = auto_reports_hour_kb()
        callbacks = self._callbacks(kb)
        assert "ar_hour_7" in callbacks
        assert "ar_hour_12" in callbacks
        assert "cmd_auto_reports" in callbacks  # cancel button

    def test_auto_reports_tz_kb(self):
        from bot.keyboards import auto_reports_tz_kb
        kb = auto_reports_tz_kb()
        callbacks = self._callbacks(kb)
        assert "ar_tz_America/New_York" in callbacks
        assert "ar_tz_UTC" in callbacks
        assert "cmd_auto_reports" in callbacks

    def test_auto_reports_type_kb(self):
        from bot.keyboards import auto_reports_type_kb
        kb = auto_reports_type_kb()
        callbacks = self._callbacks(kb)
        assert "ar_type_faults" in callbacks
        assert "ar_type_fuel" in callbacks
        assert "ar_type_health" in callbacks
        assert "ar_type_efficiency" in callbacks

    def test_quiet_hours_picker_kb(self):
        from bot.keyboards import quiet_hours_picker_kb
        kb = quiet_hours_picker_kb()
        callbacks = self._callbacks(kb)
        assert "quiet_set_6_22" in callbacks
        assert "settings_quiet" in callbacks

    def test_quiet_hours_picker_kb_with_schedules(self):
        from bot.keyboards import quiet_hours_picker_kb
        schedules = [
            {"id": 1, "start_hour": 6, "end_hour": 18, "label": "Day Shift"},
            {"id": 2, "start_hour": 18, "end_hour": 6, "label": "Night Shift"},
        ]
        kb = quiet_hours_picker_kb(schedules)
        callbacks = self._callbacks(kb)
        assert "quiet_set_6_18" in callbacks
        assert "quiet_set_18_6" in callbacks
        assert "settings_quiet_off" in callbacks
        # Hardcoded defaults should NOT appear
        assert "quiet_set_6_22" not in callbacks

    def test_user_settings_kb(self):
        from bot.keyboards import user_settings_kb
        user = User(
            id=1, telegram_id=1, account_id=1, role=Role.OWNER,
            department="", truck_num=None, alerts_on=True, is_active=True,
            created_at="", alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True,
            quiet_start=None, quiet_end=None, timezone="America/New_York",
        )
        kb = user_settings_kb(user)
        callbacks = self._callbacks(kb)
        assert "settings_quiet" in callbacks
        assert "settings_tz" in callbacks
        assert "cmd_menu" in callbacks

    def test_onboarding_kb(self):
        from bot.keyboards import onboarding_kb
        kb = onboarding_kb()
        callbacks = self._callbacks(kb)
        assert "cmd_integrate_guide" in callbacks
        assert "settings_tz" in callbacks
        assert "settings_quiet_set" in callbacks
        assert "cmd_menu" in callbacks

    def test_quiet_hours_kb_active(self):
        from bot.keyboards import quiet_hours_kb
        user = User(
            id=1, telegram_id=1, account_id=1, role=Role.OWNER,
            department="", truck_num=None, alerts_on=True, is_active=True,
            created_at="", alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True,
            quiet_start=22, quiet_end=6, timezone="America/New_York",
        )
        kb = quiet_hours_kb(user)
        callbacks = self._callbacks(kb)
        assert "settings_quiet_set" in callbacks
        assert "settings_quiet_off" in callbacks

    def test_quiet_hours_kb_inactive(self):
        from bot.keyboards import quiet_hours_kb
        user = User(
            id=1, telegram_id=1, account_id=1, role=Role.OWNER,
            department="", truck_num=None, alerts_on=True, is_active=True,
            created_at="", alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True,
            quiet_start=None, quiet_end=None, timezone="America/New_York",
        )
        kb = quiet_hours_kb(user)
        callbacks = self._callbacks(kb)
        assert "settings_quiet_set" in callbacks
        assert "settings_quiet_off" not in callbacks

    def test_quiet_hours_kb_admin_no_manage_button(self):
        """Manage button moved to Management menu; quiet_hours_kb should not have it."""
        from bot.keyboards import quiet_hours_kb
        user = User(
            id=1, telegram_id=1, account_id=1, role=Role.OWNER,
            department="", truck_num=None, alerts_on=True, is_active=True,
            created_at="", alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True,
            quiet_start=None, quiet_end=None, timezone="America/New_York",
        )
        kb = quiet_hours_kb(user)
        callbacks = self._callbacks(kb)
        assert "cmd_work_schedules" not in callbacks

    def test_quiet_hours_kb_non_admin_no_manage_button(self):
        from bot.keyboards import quiet_hours_kb
        user = User(
            id=1, telegram_id=1, account_id=1, role=Role.DRIVER,
            department="", truck_num=None, alerts_on=True, is_active=True,
            created_at="", alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True,
            quiet_start=None, quiet_end=None, timezone="America/New_York",
        )
        kb = quiet_hours_kb(user)
        callbacks = self._callbacks(kb)
        assert "cmd_work_schedules" not in callbacks

    def test_work_schedules_kb(self):
        from bot.keyboards import work_schedules_kb
        schedules = [
            {"id": 1, "start_hour": 6, "end_hour": 18, "label": "Day Shift", "target_role": "all"},
        ]
        kb = work_schedules_kb(schedules)
        callbacks = self._callbacks(kb)
        assert "wsched_view_1" in callbacks
        assert "wsched_add" in callbacks
        assert "submenu_mgmt" in callbacks  # back goes to Management

    def test_work_schedules_kb_max(self):
        from bot.keyboards import work_schedules_kb
        schedules = [{"id": i, "start_hour": 6, "end_hour": 18, "label": f"S{i}", "target_role": "all"} for i in range(10)]
        kb = work_schedules_kb(schedules)
        callbacks = self._callbacks(kb)
        assert "wsched_add" not in callbacks

    def test_work_schedules_kb_shows_role_tag(self):
        from bot.keyboards import work_schedules_kb
        schedules = [
            {"id": 1, "start_hour": 6, "end_hour": 18, "label": "Driver Shift", "target_role": "driver"},
        ]
        kb = work_schedules_kb(schedules)
        labels = [b.text for r in kb.inline_keyboard for b in r]
        assert any("[driver]" in lbl for lbl in labels)

    def test_settings_tz_kb(self):
        from bot.keyboards import settings_tz_kb
        kb = settings_tz_kb()
        callbacks = self._callbacks(kb)
        assert "set_tz_America/New_York" in callbacks
        assert "set_tz_UTC" in callbacks

    def test_auto_reports_menu_with_subscription(self):
        from bot.keyboards import auto_reports_menu_kb
        sub = {"frequency": "daily", "send_hour": 9, "timezone": "America/Chicago", "report_type": "faults"}
        kb = auto_reports_menu_kb(sub)
        callbacks = self._callbacks(kb)
        assert "ar_unsub" in callbacks

    def test_submenu_mgmt_has_no_settings(self):
        """Settings was moved to the main menu root; management submenu should not have it."""
        from bot.keyboards import submenu_mgmt_kb
        kb = submenu_mgmt_kb(Role.OWNER, has_api=True)
        callbacks = self._callbacks(kb)
        assert "cmd_settings" not in callbacks
        assert "cmd_audit" in callbacks
        assert "cmd_work_schedules" in callbacks

    def test_submenu_mgmt_driver_has_no_settings_no_audit(self):
        """Driver mgmt submenu: no settings (in root) and no audit."""
        from bot.keyboards import submenu_mgmt_kb
        kb = submenu_mgmt_kb(Role.DRIVER, has_api=False)
        callbacks = self._callbacks(kb)
        assert "cmd_settings" not in callbacks
        assert "cmd_audit" not in callbacks
        assert "cmd_work_schedules" not in callbacks


# ══════════════════════════════════════════════════════════════════
# RATE LIMITING
# ══════════════════════════════════════════════════════════════════

class TestRateLimiting:
    def test_first_call_allowed(self):
        from bot.config import check_rate_limit, _rate_limits
        _rate_limits.clear()
        assert check_rate_limit(12345, "test_cmd") is True

    def test_second_call_blocked(self):
        from bot.config import check_rate_limit, _rate_limits
        _rate_limits.clear()
        check_rate_limit(99999, "test_cmd")
        assert check_rate_limit(99999, "test_cmd") is False

    def test_different_users_independent(self):
        from bot.config import check_rate_limit, _rate_limits
        _rate_limits.clear()
        check_rate_limit(1001, "cmd")
        assert check_rate_limit(1002, "cmd") is True

    def test_different_commands_independent(self):
        from bot.config import check_rate_limit, _rate_limits
        _rate_limits.clear()
        check_rate_limit(5555, "cmd_a")
        assert check_rate_limit(5555, "cmd_b") is True


# ─────────────────────────────────────────────────────────────────
# AI USAGE TRACKING
# ─────────────────────────────────────────────────────────────────

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


class TestAIClientUsageCapture:
    """Tests for ai._capture_usage and get_last_usage."""

    def test_capture_usage_with_metadata(self):
        import ai

        class FakeMeta:
            prompt_token_count = 120
            candidates_token_count = 80
            total_token_count = 200

        class FakeResponse:
            usage_metadata = FakeMeta()

        ai._capture_usage(FakeResponse())
        usage = ai.get_last_usage()
        assert usage is not None
        assert usage["prompt_tokens"] == 120
        assert usage["reply_tokens"] == 80
        assert usage["total_tokens"] == 200

    def test_capture_usage_no_metadata(self):
        import ai

        class FakeResponse:
            pass

        ai._capture_usage(FakeResponse())
        assert ai.get_last_usage() is None

    def test_capture_usage_exception_safe(self):
        import ai

        class BadResponse:
            @property
            def usage_metadata(self):
                raise RuntimeError("boom")

        ai._capture_usage(BadResponse())
        assert ai.get_last_usage() is None

    def test_get_last_usage_cleared_after_no_meta(self):
        import ai

        # Set some usage first
        class FakeMeta:
            prompt_token_count = 10
            candidates_token_count = 5
            total_token_count = 15

        class FakeResponse:
            usage_metadata = FakeMeta()

        ai._capture_usage(FakeResponse())
        assert ai.get_last_usage() is not None

        # Now capture with no metadata — should clear
        class EmptyResponse:
            pass

        ai._capture_usage(EmptyResponse())
        assert ai.get_last_usage() is None


# ══════════════════════════════════════════════════════════════════
# ESCALATION CHAIN LOGIC
# ══════════════════════════════════════════════════════════════════

class TestReAlertConfig:
    """Tests for the unified alert architecture configuration.

    Verifies AlertSeverity enum, re-alert constants, and build_alert_keyboard.
    """

    def test_alert_severity_values(self):
        from bot.alerts import AlertSeverity
        assert AlertSeverity.CRITICAL.value == "critical"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.INFO.value == "info"

    def test_realert_constants(self):
        from bot.alerts import ACK_WINDOW_MINUTES, MAX_REALERTS, SNOOZE_MINUTES
        assert ACK_WINDOW_MINUTES > 0
        assert MAX_REALERTS >= 1
        assert SNOOZE_MINUTES > 0

    def test_build_keyboard_critical_with_ack(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity, SNOOZE_MINUTES
        kb = build_alert_keyboard(AlertSeverity.CRITICAL, "CO1", "101", ack_id=42)
        labels = [b.text for r in kb.inline_keyboard for b in r]
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r]
        assert "✅ Acknowledge" in labels
        assert "⏰ Snooze" in labels
        assert "🤖 AI Diagnose" in labels
        assert "📋 View Truck #101" in labels
        assert "ack_alert_42" in callbacks
        assert "snooze_pick_42" in callbacks
        # Default alert_type is "fault", ack_id appended
        assert "ai_diag_fault_CO1_101:42" in callbacks

    def test_build_keyboard_warning_with_ack(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(AlertSeverity.WARNING, "CO1", "202", ack_id=99)
        labels = [b.text for r in kb.inline_keyboard for b in r]
        assert "✅ Acknowledge" in labels
        assert "🤖 AI Diagnose" in labels

    def test_build_keyboard_health_type(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(
            AlertSeverity.WARNING, "CO1", "303", ack_id=10,
            alert_type="health",
        )
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r]
        assert "ai_diag_health_CO1_303:10" in callbacks

    def test_build_keyboard_fuel_type(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(
            AlertSeverity.CRITICAL, "CO1", "404", ack_id=20,
            alert_type="fuel",
        )
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r]
        assert "ai_diag_fuel_CO1_404:20" in callbacks

    def test_build_keyboard_critical_no_ack(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(AlertSeverity.CRITICAL, "CO1", "101")
        labels = [b.text for r in kb.inline_keyboard for b in r]
        assert "✅ Acknowledge" not in labels
        assert "🤖 AI Diagnose" in labels
        assert "📋 View Truck #101" in labels

    def test_build_keyboard_info_no_ack_no_ai(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(AlertSeverity.INFO, "CO1", "303")
        labels = [b.text for r in kb.inline_keyboard for b in r]
        assert "✅ Acknowledge" not in labels
        assert "🤖 AI Diagnose" not in labels
        assert "📋 View Truck #303" in labels
        assert "◀️ Main Menu" in labels

    def test_build_keyboard_has_samsara_link(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        from samsara_client import ORG_IDS
        ORG_IDS["CO1"] = "123"
        try:
            kb = build_alert_keyboard(AlertSeverity.CRITICAL, "CO1", "101", ack_id=1,
                                      vehicle_id="12345")
            urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
            assert any("cloud.samsara.com" in u for u in urls)
        finally:
            ORG_IDS.pop("CO1", None)

    def test_build_keyboard_no_samsara_link_without_id(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(AlertSeverity.CRITICAL, "CO1", "101", ack_id=1)
        urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
        assert not urls

    def test_cooldown_hours_per_type(self):
        from bot.alerts import _COOLDOWN_HOURS
        assert _COOLDOWN_HOURS["fault"] > 0
        assert _COOLDOWN_HOURS["health"] > 0
        assert _COOLDOWN_HOURS["fuel"] == 0   # uses hysteresis
        assert _COOLDOWN_HOURS["geofence"] == 0  # event-based

    def test_fuel_critical_threshold(self):
        from bot.alerts import FUEL_CRITICAL_PCT
        assert FUEL_CRITICAL_PCT == 10

    def test_health_severity_sets(self):
        from bot.alerts import _CRITICAL_HEALTH, _WARNING_HEALTH
        assert "low_oil_pressure" in _CRITICAL_HEALTH
        assert "high_coolant_temp" in _CRITICAL_HEALTH
        assert "low_battery" in _WARNING_HEALTH
        assert "low_def" in _WARNING_HEALTH
        assert "coolant_dtc" in _WARNING_HEALTH


# ══════════════════════════════════════════════════════════════════
# OPEN IN SAMSARA — deep-link buttons on alerts
# ══════════════════════════════════════════════════════════════════

class TestSamsaraDeepLinks:
    """Tests for samsara_vehicle_url helper and alert keyboard URL button."""

    def test_url_builder_fault(self):
        from samsara_client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "fault",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_health(self):
        from samsara_client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "health",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_fuel(self):
        from samsara_client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "fuel",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_events(self):
        from samsara_client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "events",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_unknown_type_fallback(self):
        from samsara_client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "unknown_type",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_no_org_returns_none(self):
        from samsara_client import samsara_vehicle_url
        assert samsara_vehicle_url("", "v999", "fault") is None
        assert samsara_vehicle_url("", "v999", "health") is None

    def test_keyboard_includes_url_button_when_org_known(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        import samsara_client
        samsara_client.ORG_IDS["TEST"] = "org123"
        try:
            kb = build_alert_keyboard(
                AlertSeverity.CRITICAL, "TEST", "T100",
                ack_id=1, alert_type="fault", vehicle_id="v555",
            )
            urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
            assert any("org123" in u and "v555" in u for u in urls)
            labels = [b.text for r in kb.inline_keyboard for b in r]
            assert any("Samsara" in lbl for lbl in labels)
        finally:
            samsara_client.ORG_IDS.pop("TEST", None)

    def test_keyboard_no_url_button_when_org_unknown(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        import samsara_client
        samsara_client.ORG_IDS.pop("NOPE", None)
        kb = build_alert_keyboard(
            AlertSeverity.CRITICAL, "NOPE", "T100",
            ack_id=1, alert_type="fault", vehicle_id="v555",
        )
        urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
        assert len(urls) == 0

    def test_keyboard_info_severity_with_url(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        import samsara_client
        samsara_client.ORG_IDS["CO2"] = "org456"
        try:
            kb = build_alert_keyboard(
                AlertSeverity.INFO, "CO2", "T200",
                alert_type="events", vehicle_id="v777",
            )
            urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
            assert len(urls) == 1
            assert "/devices/" in urls[0] and "/vehicle" in urls[0]
            labels = [b.text for r in kb.inline_keyboard for b in r]
            # INFO → no ACK, no AI Diagnose, but should have URL + View Truck + Menu
            assert "✅ Acknowledge" not in labels
            assert "🤖 AI Diagnose" not in labels
        finally:
            samsara_client.ORG_IDS.pop("CO2", None)

    def test_org_ids_dict_exists(self):
        from samsara_client import ORG_IDS
        assert isinstance(ORG_IDS, dict)

    def test_dashboard_url_config(self):
        from bot.config import SAMSARA_DASHBOARD_URL
        assert "cloud" in SAMSARA_DASHBOARD_URL
        assert "samsara.com" in SAMSARA_DASHBOARD_URL


# ══════════════════════════════════════════════════════════════════
# SHARED ACKNOWLEDGMENT
# ══════════════════════════════════════════════════════════════════

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
            next_escalation="2099-01-01T00:00:00",
        )
        ack_id2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="shared_key_1",
            message_id=2, chat_id=2002, sent_to=2002,
            next_escalation="2099-01-01T00:00:00",
        )
        # Ack the first one
        await db.acknowledge_alert(ack_id1, owner.telegram_id)
        # Both should now be acked
        unacked = await db.get_unacked_alerts()
        assert len(unacked) == 0

    async def test_different_keys_not_shared(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        ack_id1 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="key_A",
            message_id=1, chat_id=1001, sent_to=1001,
            next_escalation="2099-01-01T00:00:00",
        )
        ack_id2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v2", vehicle_name="T200",
            alert_key="key_B",
            message_id=2, chat_id=2002, sent_to=2002,
            next_escalation="2099-01-01T00:00:00",
        )
        await db.acknowledge_alert(ack_id1, owner.telegram_id)
        unacked = await db.get_unacked_alerts()
        assert len(unacked) == 1
        assert unacked[0]["alert_key"] == "key_B"


# ══════════════════════════════════════════════════════════════════
# ALERT EXPIRATION
# ══════════════════════════════════════════════════════════════════

class TestAlertExpiration:
    """Tests for auto-expiring stale alerts."""

    async def test_expire_old_alerts(self, seeded):
        db, account = seeded["db"], seeded["account"]
        # Create alert with old timestamp
        ack_id = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="expire_test",
            message_id=1, chat_id=1001, sent_to=1001,
            next_escalation="2020-01-01T00:00:00",
        )
        # Expire alerts older than now
        from datetime import datetime, timezone
        expired = await db.expire_stale_alerts(datetime.now(timezone.utc).isoformat())
        assert len(expired) == 1
        assert expired[0]["id"] == ack_id
        # Should no longer appear in unacked
        unacked = await db.get_unacked_alerts()
        assert len(unacked) == 0

    async def test_fresh_alerts_not_expired(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="fresh_test",
            message_id=1, chat_id=1001, sent_to=1001,
            next_escalation="2099-01-01T00:00:00",
        )
        # Try to expire with a past cutoff
        expired = await db.expire_stale_alerts("2020-01-01T00:00:00")
        assert len(expired) == 0


# ══════════════════════════════════════════════════════════════════
# DND ALERT QUEUE
# ══════════════════════════════════════════════════════════════════

class TestDNDAlertQueue:
    """Tests for DND alert queuing and delivery."""

    async def test_queue_dnd_alert(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        qid = await db.queue_dnd_alert(
            account_id=account.id,
            telegram_id=owner.telegram_id,
            alert_type="fault",
            vehicle_name="T100",
            alert_text="Test alert text",
        )
        assert isinstance(qid, int)
        assert qid > 0

    async def test_get_pending_dnd_alerts(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.queue_dnd_alert(account.id, owner.telegram_id, "fault", "T100", "Alert 1")
        await db.queue_dnd_alert(account.id, owner.telegram_id, "health", "T200", "Alert 2")
        pending = await db.get_pending_dnd_alerts(owner.telegram_id)
        assert len(pending) == 2

    async def test_mark_dnd_delivered(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.queue_dnd_alert(account.id, owner.telegram_id, "fault", "T100", "Alert 1")
        await db.queue_dnd_alert(account.id, owner.telegram_id, "fuel", "T200", "Alert 2")
        count = await db.mark_dnd_alerts_delivered(owner.telegram_id)
        assert count == 2
        # No more pending
        pending = await db.get_pending_dnd_alerts(owner.telegram_id)
        assert len(pending) == 0

    async def test_dnd_queue_isolation(self, seeded):
        db, account, owner, driver = seeded["db"], seeded["account"], seeded["owner"], seeded["driver"]
        await db.queue_dnd_alert(account.id, owner.telegram_id, "fault", "T100", "Owner alert")
        await db.queue_dnd_alert(account.id, driver.telegram_id, "fault", "T101", "Driver alert")
        owner_pending = await db.get_pending_dnd_alerts(owner.telegram_id)
        driver_pending = await db.get_pending_dnd_alerts(driver.telegram_id)
        assert len(owner_pending) == 1
        assert len(driver_pending) == 1


# ══════════════════════════════════════════════════════════════════
# WORK SCHEDULES
# ══════════════════════════════════════════════════════════════════

class TestWorkSchedules:
    """Tests for admin-defined working hour presets."""

    async def test_create_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_schedule(
            account_id=account.id, label="Day Shift",
            start_hour=6, end_hour=18, created_by=owner.id,
        )
        assert sched["label"] == "Day Shift"
        assert sched["start_hour"] == 6
        assert sched["end_hour"] == 18

    async def test_list_work_schedules(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_schedule(account.id, "Day Shift", 6, 18, owner.id)
        await db.create_work_schedule(account.id, "Night Shift", 18, 6, owner.id)
        schedules = await db.get_work_schedules(account.id)
        assert len(schedules) == 2

    async def test_get_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        created = await db.create_work_schedule(account.id, "Office Hours", 8, 17, owner.id)
        fetched = await db.get_work_schedule(created["id"])
        assert fetched is not None
        assert fetched["label"] == "Office Hours"

    async def test_update_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        created = await db.create_work_schedule(account.id, "Early", 5, 14, owner.id)
        await db.update_work_schedule(created["id"], label="Early Bird", start_hour=4, end_hour=13)
        updated = await db.get_work_schedule(created["id"])
        assert updated["label"] == "Early Bird"
        assert updated["start_hour"] == 4
        assert updated["end_hour"] == 13

    async def test_delete_work_schedule(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        created = await db.create_work_schedule(account.id, "Temp", 9, 17, owner.id)
        await db.delete_work_schedule(created["id"])
        result = await db.get_work_schedule(created["id"])
        assert result is None

    async def test_schedules_isolation_between_accounts(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_schedule(account.id, "Shift A", 6, 18, owner.id)
        # Different account should see nothing
        other_schedules = await db.get_work_schedules(999)
        assert len(other_schedules) == 0

    async def test_create_schedule_with_target_role(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_schedule(
            account_id=account.id, label="Driver Shift",
            start_hour=7, end_hour=19, created_by=owner.id,
            target_role="driver",
        )
        assert sched["target_role"] == "driver"
        fetched = await db.get_work_schedule(sched["id"])
        assert fetched["target_role"] == "driver"

    async def test_default_target_role_is_all(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_schedule(account.id, "General", 8, 17, owner.id)
        assert sched["target_role"] == "all"

    async def test_get_work_schedules_for_role_all(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_schedule(account.id, "All Shift", 6, 18, owner.id, target_role="all")
        await db.create_work_schedule(account.id, "Driver Only", 7, 19, owner.id, target_role="driver")
        # Driver should see both 'all' and 'driver'
        driver_schedules = await db.get_work_schedules_for_role(account.id, "driver")
        assert len(driver_schedules) == 2

    async def test_get_work_schedules_for_role_filtered(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_work_schedule(account.id, "Driver Only", 7, 19, owner.id, target_role="driver")
        await db.create_work_schedule(account.id, "Admin Only", 8, 17, owner.id, target_role="admin")
        # Admin should see only 'admin' (not 'driver')
        admin_schedules = await db.get_work_schedules_for_role(account.id, "admin")
        assert len(admin_schedules) == 1
        assert admin_schedules[0]["target_role"] == "admin"

    async def test_update_schedule_target_role(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        sched = await db.create_work_schedule(account.id, "Shift", 6, 18, owner.id)
        await db.update_work_schedule(sched["id"], target_role="fleet")
        updated = await db.get_work_schedule(sched["id"])
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
            next_escalation="2099-01-01T00:00:00",
        )
        handoff = await db.get_shift_handoff_data(account.id, owner.telegram_id)
        assert len(handoff["pending_alerts"]) == 1


# ══════════════════════════════════════════════════════════════════
# ROLE PICKER KEYBOARD
# ══════════════════════════════════════════════════════════════════

class TestRolePickerKeyboard:
    def _callbacks(self, kb):
        return [b.callback_data for r in kb.inline_keyboard for b in r if b.callback_data]

    def test_role_picker_has_all_roles(self):
        from bot.keyboards import work_schedule_role_picker_kb
        kb = work_schedule_role_picker_kb()
        callbacks = self._callbacks(kb)
        assert "wsched_role_all" in callbacks
        assert "wsched_role_owner" in callbacks
        assert "wsched_role_admin" in callbacks
        assert "wsched_role_fleet" in callbacks
        assert "wsched_role_dispatcher" in callbacks
        assert "wsched_role_driver" in callbacks

    def test_role_picker_has_back(self):
        from bot.keyboards import work_schedule_role_picker_kb
        kb = work_schedule_role_picker_kb()
        callbacks = self._callbacks(kb)
        assert "cmd_work_schedules" in callbacks

    def test_schedule_detail_has_change_role(self):
        from bot.keyboards import work_schedule_detail_kb
        kb = work_schedule_detail_kb(42)
        callbacks = self._callbacks(kb)
        assert "wsched_changerole_42" in callbacks

class TestAlertSnooze:
    """Tests for alert snooze."""

    async def test_snooze_postpones_escalation(self, seeded):
        db, account = seeded["db"], seeded["account"]
        ack_id = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="snooze_test",
            message_id=1, chat_id=1001, sent_to=1001,
            next_escalation="2025-01-01T00:00:00",
        )
        new_time = "2025-01-01T00:15:00"
        await db.snooze_alert(ack_id, new_time)
        unacked = await db.get_unacked_alerts(before="2025-01-01T00:10:00")
        assert len(unacked) == 0  # snoozed past the check time
        unacked_later = await db.get_unacked_alerts(before="2025-01-01T00:16:00")
        assert len(unacked_later) == 1

    async def test_snooze_does_not_ack(self, seeded):
        db, account = seeded["db"], seeded["account"]
        ack_id = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="snooze_no_ack",
            message_id=1, chat_id=1001, sent_to=1001,
            next_escalation="2025-01-01T00:00:00",
        )
        await db.snooze_alert(ack_id, "2099-01-01T00:00:00")
        # Still appears in full unacked list (no before filter)
        unacked = await db.get_unacked_alerts()
        assert len(unacked) == 1


# ══════════════════════════════════════════════════════════════════
# VEHICLE MAINTENANCE SUPPRESSION
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# ALERT HISTORY & PENDING
# ══════════════════════════════════════════════════════════════════

class TestAlertHistoryAndPending:
    """Tests for alert history and pending alerts queries."""

    async def test_get_alert_history(self, seeded):
        db, account = seeded["db"], seeded["account"]
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="hist_1", message_id=1, chat_id=1001,
            sent_to=1001, next_escalation="2099-01-01T00:00:00",
        )
        await db.create_alert_ack(
            account_id=account.id, alert_type="health",
            vehicle_id="v2", vehicle_name="T200",
            alert_key="hist_2", message_id=2, chat_id=1001,
            sent_to=1001, next_escalation="2099-01-01T00:00:00",
        )
        history = await db.get_alert_history(account.id)
        assert len(history) == 2

    async def test_get_pending_alerts(self, seeded):
        db, account, owner = seeded["db"], seeded["account"], seeded["owner"]
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v1", vehicle_name="T100",
            alert_key="pend_1", message_id=1, chat_id=1001,
            sent_to=1001, next_escalation="2099-01-01T00:00:00",
        )
        ack_id2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="v2", vehicle_name="T200",
            alert_key="pend_2", message_id=2, chat_id=1001,
            sent_to=1001, next_escalation="2099-01-01T00:00:00",
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
                alert_key=f"lim_{i}", message_id=i, chat_id=1001,
                sent_to=1001, next_escalation="2099-01-01T00:00:00",
            )
        history = await db.get_alert_history(account.id, limit=5)
        assert len(history) == 5


# ══════════════════════════════════════════════════════════════════
# UNSAFE PARKING DETECTION
# ══════════════════════════════════════════════════════════════════

class TestAddressClassification:
    """Tests for the address classification heuristic."""

    def test_safe_truck_stop(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Pilot Travel Center, I-95, Exit 42") == "safe"

    def test_safe_rest_area(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("I-40 Rest Area Mile Marker 150") == "safe"

    def test_safe_loves(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Love's Travel Stop #429, Hwy 10") == "safe"

    def test_safe_warehouse(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Amazon Warehouse, 123 Distribution Blvd") == "safe"

    def test_safe_terminal(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("FedEx Terminal, Industrial Park Dr") == "safe"

    def test_unsafe_highway(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("I-95 Highway, Mile Marker 87") == "unsafe"

    def test_unsafe_shoulder(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Shoulder of Route 22, Exit Ramp") == "unsafe"

    def test_unsafe_interchange(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Interstate 280 Interchange") == "unsafe"

    def test_unsafe_ramp(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Exit ramp off I-10") == "unsafe"

    def test_unknown_residential(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("123 Maple Street, Springfield, IL") == "unknown"

    def test_unknown_empty(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("") == "unknown"

    def test_safe_flying_j(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Flying J Travel Plaza, Exit 12") == "safe"

    def test_safe_petro(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Petro Stopping Center, Hwy 55") == "safe"

    def test_unsafe_freeway(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("US-101 Freeway, Northbound") == "unsafe"

    def test_unsafe_beltway(self):
        """Real Truck #238 address: Capital Beltway, Adelphi, MD, 20740."""
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Capital Beltway, Adelphi, MD, 20740") == "unsafe"

    def test_unsafe_turnpike(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("NJ Turnpike, Mile Marker 87") == "unsafe"

    def test_unsafe_expressway(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Long Island Expressway, Queens") == "unsafe"

    def test_unsafe_parkway(self):
        from bot.alerts import classify_parking_location
        assert classify_parking_location("Garden State Parkway, Rahway") == "unsafe"


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


class TestGeofenceCheck:
    """Test geofence-based parking safety."""

    def test_inside_circular_geofence(self):
        from bot.geofences import _is_inside_geofence
        gf = {
            "circularGeofence": {
                "latitude": 40.7128,
                "longitude": -74.0060,
                "radiusMeters": 500,
            }
        }
        assert _is_inside_geofence(40.7128, -74.006, gf) is True

    def test_outside_circular_geofence(self):
        from bot.geofences import _is_inside_geofence
        gf = {
            "circularGeofence": {
                "latitude": 40.7128,
                "longitude": -74.0060,
                "radiusMeters": 100,
            }
        }
        # Far away
        assert _is_inside_geofence(41.0, -73.0, gf) is False

    def test_is_inside_any_geofence(self):
        from bot.alerts import _is_inside_any_geofence
        geofences = [
            {"circularGeofence": {"latitude": 40.0, "longitude": -74.0, "radiusMeters": 100}},
            {"circularGeofence": {"latitude": 41.0, "longitude": -73.0, "radiusMeters": 500}},
        ]
        # Inside second geofence
        assert _is_inside_any_geofence(41.0, -73.0, geofences) is True
        # Outside all
        assert _is_inside_any_geofence(45.0, -80.0, geofences) is False


class TestParkingMapRender:
    """Test satellite + road map rendering for AI vision analysis."""

    def test_render_parking_map_returns_bytes(self):
        """_render_parking_map should return PNG bytes for valid coords."""
        from bot.alerts import _render_parking_map
        from unittest.mock import patch, MagicMock
        import io

        # Mock staticmap and PIL to avoid actual HTTP tile fetching
        mock_img = MagicMock()
        mock_img.convert.return_value = mock_img

        mock_map_instance = MagicMock()
        mock_map_instance.render.return_value = mock_img

        with patch("staticmap.StaticMap", return_value=mock_map_instance) as MockMap, \
             patch("PIL.Image.new") as MockPILNew, \
             patch("PIL.Image.alpha_composite", return_value=mock_img):
            combined_mock = MagicMock()
            def fake_save(buf, format):
                buf.write(b"\x89PNG_combined_map_data")
            combined_mock.save = fake_save
            MockPILNew.return_value = combined_mock

            result = _render_parking_map(40.7128, -74.006)

        assert result is not None
        assert isinstance(result, bytes)
        assert len(result) > 0
        # StaticMap called 3 times (satellite + labels overlay + road map)
        assert MockMap.call_count == 3

    def test_render_parking_map_catches_errors(self):
        """Should return None if tile fetch / rendering fails."""
        from bot.alerts import _render_parking_map
        from unittest.mock import patch

        with patch("staticmap.StaticMap", side_effect=Exception("tile error")):
            result = _render_parking_map(40.7128, -74.006)
        assert result is None

    @pytest.mark.asyncio
    async def test_ai_analysis_with_map_uses_vision(self):
        """When map renders successfully, should call generate_with_vision."""
        from bot.alerts import _get_ai_parking_analysis
        from unittest.mock import patch, AsyncMock, MagicMock

        mock_map = b"\x89PNG_fake_map"
        mock_vision_response = (
            "CLASSIFICATION: UNSAFE\n"
            "CONFIDENCE: HIGH\n"
            "REASON: Truck is on the highway shoulder near an interchange."
        )

        with patch("bot.alerts.parking._render_parking_map", return_value=mock_map), \
             patch("ai.is_configured", return_value=True), \
             patch("ai.generate_with_vision", new_callable=AsyncMock,
                   return_value=mock_vision_response) as mock_gv, \
             patch("ai.get_last_usage", return_value=None):
            result = await _get_ai_parking_analysis(
                "238", "Capital Beltway, Adelphi, MD, 20740",
                39.018952, -76.950656, 3.5,
            )

        assert "UNSAFE" in result
        assert "highway shoulder" in result
        mock_gv.assert_called_once()
        # Verify image bytes were passed
        assert mock_gv.call_args[0][1] == mock_map

    @pytest.mark.asyncio
    async def test_ai_analysis_fallback_to_text(self):
        """When map render fails, should fall back to text-only generate."""
        from bot.alerts import _get_ai_parking_analysis
        from unittest.mock import patch, AsyncMock

        with patch("bot.alerts.parking._render_parking_map", return_value=None), \
             patch("ai.is_configured", return_value=True), \
             patch("ai.generate", new_callable=AsyncMock,
                   return_value="SAFE. This is a truck stop.") as mock_gen, \
             patch("ai.get_last_usage", return_value=None):
            result = await _get_ai_parking_analysis("100", "Loves #42", 35.0, -90.0, 4.0)

        assert "SAFE" in result
        mock_gen.assert_called_once()


class TestParkingAlertFormat:
    """Test parking alert message formatting."""

    def test_format_warning(self):
        from bot.alerts import _format_parking_alert, AlertSeverity
        text = _format_parking_alert(
            "238", "Capital Beltway, Adelphi, MD, 20740",
            39.018952, -76.950656,
            3.5, "unsafe", "", AlertSeverity.WARNING,
        )
        assert "WARNING" in text
        assert "#238" in text
        assert "Capital Beltway" in text
        assert "3.5h" in text
        assert "maps.google.com" in text

    def test_format_critical(self):
        from bot.alerts import _format_parking_alert, AlertSeverity
        text = _format_parking_alert(
            "238", "Capital Beltway, Adelphi, MD, 20740",
            39.018952, -76.950656,
            12.0, "unsafe",
            "UNSAFE. Truck is on the Capital Beltway highway interchange shoulder.",
            AlertSeverity.CRITICAL,
        )
        assert "CRITICAL" in text
        assert "Immediate attention" in text
        assert "AI Analysis" in text

    def test_format_long_duration_days(self):
        from bot.alerts import _format_parking_alert, AlertSeverity
        text = _format_parking_alert(
            "238", "Capital Beltway, Adelphi, MD, 20740",
            39.018952, -76.950656,
            48.0, "unknown", "", AlertSeverity.WARNING,
        )
        assert "2.0 days" in text


class TestParkingEventsKeyboard:
    """Test parking events keyboard builder."""

    def test_empty_events(self):
        from bot.keyboards import parking_events_kb
        kb = parking_events_kb([])
        buttons = [b.text for row in kb.inline_keyboard for b in row]
        assert any("No vehicles need attention" in b for b in buttons)
        assert any("Refresh" in b for b in buttons)

    def test_with_events(self):
        from bot.keyboards import parking_events_kb
        events = [
            {"id": 1, "vehicle_name": "T100", "duration_hours": 5.0, "location_class": "unsafe"},
            {"id": 2, "vehicle_name": "T200", "duration_hours": 26.0, "location_class": "unknown"},
        ]
        kb = parking_events_kb(events)
        buttons = [b.text for row in kb.inline_keyboard for b in row]
        assert any("T100" in b for b in buttons)
        assert any("T200" in b for b in buttons)

    def test_tools_menu_has_parking(self):
        from bot.keyboards import submenu_tools_kb
        kb = submenu_tools_kb(Role.OWNER)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "cmd_parking_events" in callbacks


class TestParkingAlertSubscription:
    """Test alert_parking and ai_parking subscription toggles."""

    def test_alert_settings_has_parking_toggle(self):
        from bot.keyboards import alert_settings_kb
        from database import User, Role
        user = User(
            id=1, telegram_id=111, account_id=1, role=Role.OWNER,
            department="general", truck_num=None, alerts_on=True,
            is_active=True, created_at="2025-01-01",
        )
        kb = alert_settings_kb(user)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "alert_toggle_parking" in callbacks

    def test_alert_settings_parking_icon_on(self):
        from bot.keyboards import alert_settings_kb
        from database import User, Role
        user = User(
            id=1, telegram_id=111, account_id=1, role=Role.OWNER,
            department="general", truck_num=None, alerts_on=True,
            is_active=True, created_at="2025-01-01", alert_parking=True,
        )
        kb = alert_settings_kb(user)
        parking_btn = None
        for row in kb.inline_keyboard:
            for b in row:
                if b.callback_data == "alert_toggle_parking":
                    parking_btn = b
        assert parking_btn is not None
        assert "✅" in parking_btn.text

    def test_ai_alerts_has_parking_toggle(self):
        from bot.ai import _ai_alerts_kb
        from database import User, Role
        user = User(
            id=1, telegram_id=111, account_id=1, role=Role.OWNER,
            department="general", truck_num=None, alerts_on=True,
            is_active=True, created_at="2025-01-01",
        )
        kb = _ai_alerts_kb(user)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "ai_toggle_parking" in callbacks

    @pytest.mark.asyncio
    async def test_parking_subscriber_type_in_db(self, seeded):
        """The get_all_typed_subscribers should accept 'parking' type."""
        db = seeded["db"]
        # Should not raise — 'parking' is a valid type
        subs = await db.get_all_typed_subscribers("parking")
        assert isinstance(subs, list)

    @pytest.mark.asyncio
    async def test_invalid_subscriber_type_rejected(self, seeded):
        """Unknown alert types should return empty list."""
        db = seeded["db"]
        subs = await db.get_all_typed_subscribers("nonexistent")
        assert subs == []
