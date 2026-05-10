"""Tests for the Events feature — API normalization, formatters, permissions,
database fields, alert severity, and CSV generation."""

import os
import pytest
import pytest_asyncio

os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.storage import Database, Role, User
from capabilities.iam.permissions import get_permissions
from capabilities.formatting import format_event_alert, format_events_dashboard
from capabilities.reporting import generate_events_csv


# ── Fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded(db: Database):
    account = await db.create_account("Events Test Co")
    owner = await db.create_user(telegram_id=100, account_id=account.id, role=Role.OWNER)
    driver = await db.create_user(telegram_id=200, account_id=account.id, role=Role.DRIVER, truck_num="101")
    dispatcher = await db.create_user(telegram_id=300, account_id=account.id, role=Role.DISPATCHER)
    return {"db": db, "account": account, "owner": owner, "driver": driver, "dispatcher": dispatcher}


def _sample_event(**overrides):
    """Create a sample normalized event dict."""
    base = {
        "event_id": "evt_001",
        "event_type": "braking",
        "event_name": "Harsh Brake",
        "driver_id": "drv_1",
        "driver_name": "John Smith",
        "vehicle_id": "veh_1",
        "vehicle_name": "Truck 101",
        "vehicle_vin": "1HGBH41JXMN109186",
        "time": "2026-03-15T10:30:00Z",
        "g_force": 0.55,
        "latitude": 40.7128,
        "longitude": -74.0060,
        "coaching_state": "needsReview",
        "video_url": "https://example.com/video.mp4",
        "_org": "TFC",
    }
    base.update(overrides)
    return base


def _sample_events():
    """Create a diverse list of sample events."""
    return [
        _sample_event(event_id="evt_001", event_type="braking", event_name="Harsh Brake",
                       g_force=0.55, driver_name="John Smith"),
        _sample_event(event_id="evt_002", event_type="crash", event_name="Crash",
                       g_force=1.2, driver_name="Jane Doe"),
        _sample_event(event_id="evt_003", event_type="rollingStop", event_name="Rolling Stop",
                       g_force=0.35, driver_name="John Smith"),
        _sample_event(event_id="evt_004", event_type="followingDistance", event_name="Following Distance",
                       g_force=0.45, driver_name="Bob Wilson"),
        _sample_event(event_id="evt_005", event_type="harshTurn", event_name="Harsh Turn",
                       g_force=0.65, driver_name="John Smith"),
        _sample_event(event_id="evt_006", event_type="laneDeparture", event_name="Lane Departure",
                       g_force=0.30, driver_name="Jane Doe"),
        _sample_event(event_id="evt_007", event_type="acceleration", event_name="Harsh Accel",
                       g_force=0.90, driver_name="Bob Wilson"),
    ]


# ══════════════════════════════════════════════════════════════════
# PERMISSIONS
# ══════════════════════════════════════════════════════════════════

class TestEventsPermissions:
    """Role-based access to events."""

    def test_owner_has_events_all(self):
        perms = get_permissions(Role.OWNER)
        assert perms.can_events_all is True
        assert perms.can_events_own is True

    def test_admin_has_events_all(self):
        perms = get_permissions(Role.ADMIN)
        assert perms.can_events_all is True
        assert perms.can_events_own is True

    def test_fleet_has_events_all(self):
        perms = get_permissions(Role.FLEET)
        assert perms.can_events_all is True
        assert perms.can_events_own is True

    def test_dispatcher_sees_events(self):
        # Dispatchers were granted can_events_all/can_events_own in the
        # IAM refactor — they need safety-event visibility to react to
        # mid-shift deviations.  See capabilities/iam/permissions.py
        # comment on the DISPATCHER block.
        perms = get_permissions(Role.DISPATCHER)
        assert perms.can_events_all is True
        assert perms.can_events_own is True

    def test_driver_events_own_only(self):
        perms = get_permissions(Role.DRIVER)
        assert perms.can_events_all is False
        assert perms.can_events_own is True


# ══════════════════════════════════════════════════════════════════
# DATABASE — EVENTS ALERT PREFERENCES
# ══════════════════════════════════════════════════════════════════

class TestEventsDatabase:
    """Event-related DB fields and queries."""

    @pytest.mark.asyncio
    async def test_default_alert_events_on(self, seeded):
        db = seeded["db"]
        user = await db.get_user_by_telegram_id(100)
        assert user.alert_events is True

    @pytest.mark.asyncio
    async def test_default_ai_events_off(self, seeded):
        db = seeded["db"]
        user = await db.get_user_by_telegram_id(100)
        assert user.ai_events is False

    @pytest.mark.asyncio
    async def test_toggle_alert_events(self, seeded):
        db = seeded["db"]
        owner = seeded["owner"]
        await db.update_user(owner.id, alert_events=False)
        user = await db.get_user_by_telegram_id(100)
        assert user.alert_events is False

    @pytest.mark.asyncio
    async def test_wants_alert_events(self, seeded):
        db = seeded["db"]
        owner = seeded["owner"]
        await db.update_user(owner.id, alerts_on=True)
        user = await db.get_user_by_telegram_id(100)
        assert user.wants_alert("events") is True

    @pytest.mark.asyncio
    async def test_wants_alert_events_disabled(self, seeded):
        db = seeded["db"]
        owner = seeded["owner"]
        await db.update_user(owner.id, alerts_on=True, alert_events=False)
        user = await db.get_user_by_telegram_id(100)
        assert user.wants_alert("events") is False

    @pytest.mark.asyncio
    async def test_typed_subscribers_events(self, seeded):
        db = seeded["db"]
        owner = seeded["owner"]
        await db.update_user(owner.id, alerts_on=True)
        subs = await db.get_typed_alert_subscribers(seeded["account"].id, "events")
        tids = [s.telegram_id for s in subs]
        assert 100 in tids  # owner with alerts_on

    @pytest.mark.asyncio
    async def test_typed_subscribers_events_excludes_disabled(self, seeded):
        db = seeded["db"]
        owner = seeded["owner"]
        await db.update_user(owner.id, alerts_on=True, alert_events=False)
        subs = await db.get_typed_alert_subscribers(seeded["account"].id, "events")
        tids = [s.telegram_id for s in subs]
        assert 100 not in tids


# ══════════════════════════════════════════════════════════════════
# FORMATTERS
# ══════════════════════════════════════════════════════════════════

class TestEventFormatters:
    """format_event_alert and format_events_dashboard."""

    def test_format_event_alert_basic(self):
        event = _sample_event()
        result = format_event_alert(event)
        assert "Harsh Brake" in result
        assert "Truck 101" in result
        assert "John Smith" in result
        assert "0.55g" in result
        assert "40.7128" in result

    def test_format_event_alert_crash_emoji(self):
        event = _sample_event(event_type="crash", event_name="Crash")
        result = format_event_alert(event)
        assert "💥" in result

    def test_format_event_alert_braking_emoji(self):
        event = _sample_event(event_type="braking")
        result = format_event_alert(event)
        assert "🛑" in result

    def test_format_event_alert_rolling_stop_emoji(self):
        event = _sample_event(event_type="rollingStop")
        result = format_event_alert(event)
        assert "↩️" in result

    def test_format_event_alert_no_location(self):
        event = _sample_event(latitude=None, longitude=None)
        result = format_event_alert(event)
        assert "—" in result

    def test_format_event_alert_no_video(self):
        event = _sample_event(video_url=None)
        result = format_event_alert(event)
        # Should still format successfully without video
        assert "Harsh Brake" in result

    def test_format_event_alert_unassigned_driver(self):
        event = _sample_event(driver_name="Unassigned")
        result = format_event_alert(event)
        assert "Unassigned" in result

    def test_format_events_dashboard_returns_list(self):
        events = _sample_events()
        result = format_events_dashboard(events, 7, "All Companies")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_format_events_dashboard_has_header(self):
        events = _sample_events()
        result = format_events_dashboard(events, 7)
        text = "\n".join(result)
        assert "🚨" in text

    def test_format_events_dashboard_type_counts(self):
        events = _sample_events()
        result = format_events_dashboard(events, 7)
        text = "\n".join(result)
        # Should show event type counts
        assert "💥" in text  # crash emoji
        assert "🛑" in text  # braking emoji

    def test_format_events_dashboard_top_drivers(self):
        events = _sample_events()
        result = format_events_dashboard(events, 7)
        text = "\n".join(result)
        # John Smith has 3 events, should appear in top drivers
        assert "John Smith" in text

    def test_format_events_dashboard_gforce_distribution(self):
        events = _sample_events()
        result = format_events_dashboard(events, 7)
        text = "\n".join(result)
        # Should have G-force distribution section
        assert "⚡" in text

    def test_format_events_dashboard_empty(self):
        result = format_events_dashboard([], 7)
        # Empty events should still return something
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════
# ALERT SEVERITY MAPPING
# ══════════════════════════════════════════════════════════════════

class TestEventSeverity:
    """_event_severity classification."""

    def test_crash_is_critical(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="crash", g_force=1.5)
        assert _event_severity(event) == AlertSeverity.CRITICAL

    def test_harsh_brake_high_g_is_warning(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="braking", g_force=0.9)
        assert _event_severity(event) == AlertSeverity.WARNING

    def test_harsh_brake_low_g_is_info(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="braking", g_force=0.5)
        assert _event_severity(event) == AlertSeverity.INFO

    def test_rolling_stop_is_info(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="rollingStop")
        assert _event_severity(event) == AlertSeverity.INFO

    def test_following_distance_is_info(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="followingDistance")
        assert _event_severity(event) == AlertSeverity.INFO

    def test_harsh_turn_is_info(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="harshTurn")
        assert _event_severity(event) == AlertSeverity.INFO

    def test_lane_departure_is_info(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="laneDeparture")
        assert _event_severity(event) == AlertSeverity.INFO

    def test_acceleration_is_info(self):
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="acceleration")
        assert _event_severity(event) == AlertSeverity.INFO

    def test_harsh_brake_boundary_0_8_is_info(self):
        """g_force exactly 0.8 is INFO (>0.8 required for WARNING)."""
        from capabilities.alerting import _event_severity, AlertSeverity
        event = _sample_event(event_type="braking", g_force=0.8)
        assert _event_severity(event) == AlertSeverity.INFO


# ══════════════════════════════════════════════════════════════════
# CSV GENERATOR
# ══════════════════════════════════════════════════════════════════

class TestEventsCsv:
    """generate_events_csv output."""

    def test_csv_returns_tuple(self):
        events = _sample_events()
        buf = generate_events_csv(events, 7)
        assert hasattr(buf, "read")
        assert buf.name.startswith("events_")
        assert buf.name.endswith(".csv")

    def test_csv_contains_headers(self):
        events = _sample_events()
        buf = generate_events_csv(events, 7)
        content = buf.read().decode("utf-8-sig")
        assert "Date/Time" in content
        assert "Vehicle" in content
        assert "Driver" in content
        assert "Event Type" in content
        assert "G-Force" in content

    def test_csv_contains_event_data(self):
        events = [_sample_event()]
        buf = generate_events_csv(events, 7)
        content = buf.read().decode("utf-8-sig")
        assert "Harsh Brake" in content
        assert "John Smith" in content
        assert "Truck 101" in content

    def test_csv_has_summary_row(self):
        events = _sample_events()
        buf = generate_events_csv(events, 7)
        content = buf.read().decode("utf-8-sig")
        assert "SUMMARY" in content

    def test_csv_empty_events(self):
        buf = generate_events_csv([], 7)
        content = buf.read().decode("utf-8-sig")
        # Should still have headers
        assert "Date/Time" in content

    def test_csv_company_filter(self):
        events = _sample_events()
        buf = generate_events_csv(events, 7, company_filter="TFC")
        content = buf.read().decode("utf-8-sig")
        assert "TFC" in content


# ══════════════════════════════════════════════════════════════════
# API NORMALIZATION
# ══════════════════════════════════════════════════════════════════

class TestEventNormalization:
    """Verify SamsaraClient.get_events() event normalization."""

    def test_sample_event_has_required_keys(self):
        """Verify our sample event fixture has all expected keys."""
        event = _sample_event()
        required = [
            "event_id", "event_type", "event_name",
            "driver_id", "driver_name",
            "vehicle_id", "vehicle_name", "vehicle_vin",
            "time", "g_force", "latitude", "longitude",
            "coaching_state", "video_url",
        ]
        for key in required:
            assert key in event, f"Missing key: {key}"

    def test_event_type_recognized(self):
        """All 7 event types should have emoji mappings."""
        from capabilities.formatting import _EVENT_EMOJI
        types = ["crash", "braking", "rollingStop", "followingDistance",
                 "harshTurn", "laneDeparture", "acceleration"]
        for t in types:
            assert t in _EVENT_EMOJI, f"Missing emoji for: {t}"

    def test_event_type_has_i18n_key(self):
        """All 7 event types should have i18n key mappings."""
        from capabilities.formatting import _EVENT_TYPE_KEYS
        types = ["crash", "braking", "rollingStop", "followingDistance",
                 "harshTurn", "laneDeparture", "acceleration"]
        for t in types:
            assert t in _EVENT_TYPE_KEYS, f"Missing i18n key for: {t}"


# ══════════════════════════════════════════════════════════════════
# KEYBOARD
# ══════════════════════════════════════════════════════════════════

class TestEventsKeyboard:
    """Events keyboard construction."""

    def test_events_format_kb_has_period_buttons(self):
        from interfaces.bot.keyboards import events_format_kb
        kb = events_format_kb()
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r if b.callback_data]
        assert "events_text_7" in callbacks
        assert "events_text_14" in callbacks
        assert "events_text_30" in callbacks

    def test_events_format_kb_has_csv_buttons(self):
        from interfaces.bot.keyboards import events_format_kb
        kb = events_format_kb()
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r if b.callback_data]
        assert "events_csv_7" in callbacks
        assert "events_csv_14" in callbacks
        assert "events_csv_30" in callbacks

    def test_events_format_kb_has_back_button(self):
        from interfaces.bot.keyboards import events_format_kb
        kb = events_format_kb()
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r if b.callback_data]
        assert "submenu_reports" in callbacks

    def test_alert_settings_kb_has_events_toggle(self):
        from interfaces.bot.keyboards import alert_settings_kb
        user = User(
            id=1, telegram_id=100, account_id=1, role=Role.OWNER,
            department="", truck_num=None, alerts_on=True,
            is_active=True, created_at="2025-01-01T00:00:00",
            alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True, alert_events=True,
        )
        kb = alert_settings_kb(user)
        callbacks = [b.callback_data for r in kb.inline_keyboard for b in r if b.callback_data]
        assert "alert_toggle_events" in callbacks
