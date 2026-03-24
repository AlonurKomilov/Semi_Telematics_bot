"""Tests for new automation features: quiet hours, alert acks, audit log,
auto reports, settings keyboards, onboarding keyboard, rate limiting."""

import os
import pytest
import pytest_asyncio

os.environ.setdefault("ENCRYPTION_KEY", "")

from database import Database, Role, User


# ── Fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    await database.initialize()
    yield database
    await database.close()


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
        await db.update_user(user.id, quiet_start=22, quiet_end=6, timezone="UTC")
        updated = await db.get_user(user.id)
        # Mock datetime.now so that the local hour is 23
        fake_now = real_dt(2025, 6, 15, 23, 0, 0, tzinfo=tz.utc)
        original_dt = real_dt

        class FakeDatetime(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now.astimezone(tz_val) if tz_val else fake_now

        with patch("database.datetime", FakeDatetime):
            assert updated.is_in_quiet_hours() is True

    async def test_is_in_quiet_hours_outside(self, seeded):
        from unittest.mock import patch
        from datetime import datetime as real_dt, timezone as tz
        db, user = seeded["db"], seeded["owner"]
        await db.update_user(user.id, quiet_start=22, quiet_end=6, timezone="UTC")
        updated = await db.get_user(user.id)
        # Mock current time to 12:00 UTC
        fake_now = real_dt(2025, 6, 15, 12, 0, 0, tzinfo=tz.utc)

        class FakeDatetime(real_dt):
            @classmethod
            def now(cls, tz_val=None):
                return fake_now.astimezone(tz_val) if tz_val else fake_now

        with patch("database.datetime", FakeDatetime):
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
        assert "quiet_set_22_6" in callbacks
        assert "cmd_settings" in callbacks

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

    def test_submenu_mgmt_has_settings(self):
        from bot.keyboards import submenu_mgmt_kb
        kb = submenu_mgmt_kb(Role.OWNER, has_api=True)
        callbacks = self._callbacks(kb)
        assert "cmd_settings" in callbacks
        assert "cmd_audit" in callbacks

    def test_submenu_mgmt_driver_has_settings_no_audit(self):
        from bot.keyboards import submenu_mgmt_kb
        kb = submenu_mgmt_kb(Role.DRIVER, has_api=False)
        callbacks = self._callbacks(kb)
        assert "cmd_settings" in callbacks
        assert "cmd_audit" not in callbacks


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
    """Tests for ai_client._capture_usage and get_last_usage."""

    def test_capture_usage_with_metadata(self):
        import ai_client

        class FakeMeta:
            prompt_token_count = 120
            candidates_token_count = 80
            total_token_count = 200

        class FakeResponse:
            usage_metadata = FakeMeta()

        ai_client._capture_usage(FakeResponse())
        usage = ai_client.get_last_usage()
        assert usage is not None
        assert usage["prompt_tokens"] == 120
        assert usage["reply_tokens"] == 80
        assert usage["total_tokens"] == 200

    def test_capture_usage_no_metadata(self):
        import ai_client

        class FakeResponse:
            pass

        ai_client._capture_usage(FakeResponse())
        assert ai_client.get_last_usage() is None

    def test_capture_usage_exception_safe(self):
        import ai_client

        class BadResponse:
            @property
            def usage_metadata(self):
                raise RuntimeError("boom")

        ai_client._capture_usage(BadResponse())
        assert ai_client.get_last_usage() is None

    def test_get_last_usage_cleared_after_no_meta(self):
        import ai_client

        # Set some usage first
        class FakeMeta:
            prompt_token_count = 10
            candidates_token_count = 5
            total_token_count = 15

        class FakeResponse:
            usage_metadata = FakeMeta()

        ai_client._capture_usage(FakeResponse())
        assert ai_client.get_last_usage() is not None

        # Now capture with no metadata — should clear
        class EmptyResponse:
            pass

        ai_client._capture_usage(EmptyResponse())
        assert ai_client.get_last_usage() is None


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
        assert f"⏰ Snooze {SNOOZE_MINUTES} min" in labels
        assert "🤖 AI Diagnose" in labels
        assert "📋 View Truck #101" in labels
        assert "ack_alert_42" in callbacks

    def test_build_keyboard_warning_with_ack(self):
        from bot.alerts import build_alert_keyboard, AlertSeverity
        kb = build_alert_keyboard(AlertSeverity.WARNING, "CO1", "202", ack_id=99)
        labels = [b.text for r in kb.inline_keyboard for b in r]
        assert "✅ Acknowledge" in labels
        assert "🤖 AI Diagnose" in labels

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
        kb = build_alert_keyboard(AlertSeverity.CRITICAL, "CO1", "101", ack_id=1,
                                  samsara_url="https://cloud.samsara.com/o/123/devices/12345/vehicle")
        urls = [b.url for r in kb.inline_keyboard for b in r if b.url]
        labels = [b.text for r in kb.inline_keyboard for b in r if b.url]
        assert any("cloud.samsara.com" in u for u in urls)
        assert "🔗 Open in Samsara" in labels

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
# ALERT SNOOZE
# ══════════════════════════════════════════════════════════════════

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
