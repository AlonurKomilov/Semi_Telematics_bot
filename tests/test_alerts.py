"""Tests for the enhanced alert system — formatters, preferences, keyboards."""

import os
import pytest
import pytest_asyncio

os.environ.setdefault("ENCRYPTION_KEY", "")

from adapters.storage import Role, User
from capabilities.formatting import (
    format_critical_fault_alert,
    format_health_alert,
    format_low_fuel_alert,
)
from interfaces.bot.keyboards import alert_settings_kb


# ── Fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seeded_db(db):
    account = await db.create_account("Alert Test Co")
    company = await db.add_company(
        account_id=account.id,
        code="ATC",
        samsara_api_key="key_123",
        display_name="Alert Test",
    )
    owner = await db.create_user(telegram_id=100, account_id=account.id, role=Role.OWNER)
    admin = await db.create_user(telegram_id=200, account_id=account.id, role=Role.ADMIN)
    driver = await db.create_user(telegram_id=300, account_id=account.id, role=Role.DRIVER)
    return db, account, company, owner, admin, driver


# ── Helpers ───────────────────────────────────────────────────────

def _all_callbacks(markup):
    return [b.callback_data for r in markup.inline_keyboard for b in r if b.callback_data]


def _all_labels(markup):
    return [b.text for r in markup.inline_keyboard for b in r]


# ══════════════════════════════════════════════════════════════════
# USER ALERT PREFERENCES
# ══════════════════════════════════════════════════════════════════

class TestAlertPreferences:
    """User.wants_alert() and typed subscriber queries."""

    @pytest.mark.asyncio
    async def test_default_alert_prefs_all_true(self, seeded_db):
        db, account, _, owner, _, _ = seeded_db
        user = await db.get_user_by_telegram_id(100)
        assert user.alert_faults is True
        assert user.alert_health is True
        assert user.alert_fuel is True
        assert user.alert_geofence is True

    @pytest.mark.asyncio
    async def test_wants_alert_requires_alerts_on(self, seeded_db):
        db, _, _, owner, _, _ = seeded_db
        # alerts_on defaults to False → wants_alert should be False
        assert owner.wants_alert("faults") is False
        await db.update_user(owner.id, alerts_on=True)
        user = await db.get_user_by_telegram_id(100)
        assert user.wants_alert("faults") is True

    @pytest.mark.asyncio
    async def test_wants_alert_per_type_toggle(self, seeded_db):
        db, _, _, owner, _, _ = seeded_db
        await db.update_user(owner.id, alerts_on=True, alert_health=False)
        user = await db.get_user_by_telegram_id(100)
        assert user.wants_alert("faults") is True
        assert user.wants_alert("health") is False
        assert user.wants_alert("fuel") is True

    @pytest.mark.asyncio
    async def test_wants_alert_unknown_type_defaults_true(self, seeded_db):
        """Unknown alert types default to True at the User level.
        The query layer (get_typed_alert_subscribers) rejects invalid types.
        """
        db, _, _, owner, _, _ = seeded_db
        await db.update_user(owner.id, alerts_on=True)
        user = await db.get_user_by_telegram_id(100)
        assert user.wants_alert("unknown_type") is True

    @pytest.mark.asyncio
    async def test_typed_subscribers_filters_by_type(self, seeded_db):
        db, account, _, owner, admin, driver = seeded_db
        # Enable alerts for owner and admin, not driver
        await db.update_user(owner.id, alerts_on=True)
        await db.update_user(admin.id, alerts_on=True, alert_health=False)
        await db.update_user(driver.id, alerts_on=False)

        fault_subs = await db.get_typed_alert_subscribers(account.id, "faults")
        assert len(fault_subs) == 2  # owner + admin

        health_subs = await db.get_typed_alert_subscribers(account.id, "health")
        assert len(health_subs) == 1  # owner only (admin disabled health)

    @pytest.mark.asyncio
    async def test_all_typed_subscribers(self, seeded_db):
        db, account, _, owner, admin, _ = seeded_db
        await db.update_user(owner.id, alerts_on=True)
        await db.update_user(admin.id, alerts_on=True, alert_fuel=False)

        fuel_subs = await db.get_all_typed_subscribers("fuel")
        assert len(fuel_subs) == 1  # only owner

    @pytest.mark.asyncio
    async def test_typed_subscribers_rejects_invalid_type(self, seeded_db):
        db, account, _, _, _, _ = seeded_db
        result = await db.get_typed_alert_subscribers(account.id, "invalid")
        assert result == []

    @pytest.mark.asyncio
    async def test_all_typed_subscribers_rejects_invalid(self, seeded_db):
        db, _, _, _, _, _ = seeded_db
        result = await db.get_all_typed_subscribers("sql_injection")
        assert result == []


# ══════════════════════════════════════════════════════════════════
# ALERT FORMATTERS
# ══════════════════════════════════════════════════════════════════

class TestCriticalFaultFormatter:
    """format_critical_fault_alert — STOP/PROTECT/EMISSIONS."""

    def test_basic_output_contains_critical(self):
        vehicle = {"name": "101", "location": {}, "_org": "CO1"}
        dtcs = [{"spnDescription": "Engine Temp", "fmiDescription": "Most Severe",
                 "sourceAddressName": "ECU"}]
        lights = {"stopIsOn": True}
        result = format_critical_fault_alert(vehicle, dtcs, lights)
        assert "🔴 CRITICAL" in result
        assert "Fault Detected" in result
        assert "Truck #101" in result
        assert "🛑 STOP" in result
        assert "Engine Temp" in result

    def test_multiple_lights(self):
        vehicle = {"name": "202", "location": {}, "_org": "CO1"}
        dtcs = [{"spnDescription": "Oil", "fmiDescription": "Warn",
                 "sourceAddressName": "TCU"}]
        lights = {"stopIsOn": True, "protectIsOn": True, "emissionsIsOn": True}
        result = format_critical_fault_alert(vehicle, dtcs, lights)
        assert "🛑 STOP" in result
        assert "🟡 PROTECT" in result
        assert "⚠️ EMISSIONS" in result

    def test_company_display_when_multi(self):
        vehicle = {"name": "303", "location": {}, "_org": "CO1"}
        dtcs = [{"spnDescription": "Test", "fmiDescription": "Info",
                 "sourceAddressName": "ECU"}]
        lights = {}
        result = format_critical_fault_alert(vehicle, dtcs, lights, show_company=True)
        assert "CO1" in result

    def test_no_lights_still_renders(self):
        vehicle = {"name": "404", "location": {}, "_org": "CO1"}
        dtcs = [{"spnDescription": "Sensor", "fmiDescription": "Most Severe",
                 "sourceAddressName": "ECU"}]
        lights = {}
        result = format_critical_fault_alert(vehicle, dtcs, lights)
        assert "🔴 CRITICAL" in result
        assert "Fault Detected" in result


class TestHealthAlertFormatter:
    """format_health_alert — battery, oil, coolant, DEF."""

    def test_critical_health_header(self):
        vehicle = {"name": "501", "_org": "CO1"}
        health = {"battery_v": 11.5, "oil_psi": 8}
        alerts = ["low_battery", "low_oil_pressure"]
        result = format_health_alert(vehicle, alerts, health)
        assert "🔴 CRITICAL" in result
        assert "Engine Health" in result
        assert "11.5V" in result
        assert "8 psi" in result

    def test_warning_header_for_def(self):
        vehicle = {"name": "502", "_org": "CO1"}
        health = {"def_pct": 5.2}
        alerts = ["low_def"]
        result = format_health_alert(vehicle, alerts, health)
        assert "🟠 WARNING" in result
        assert "Engine Health" in result
        assert "5.2%" in result

    def test_coolant_dtc_alert(self):
        vehicle = {"name": "503", "_org": "CO1"}
        health = {}
        alerts = ["coolant_dtc"]
        result = format_health_alert(vehicle, alerts, health)
        assert "Coolant System Fault" in result
        assert "J1939" in result

    def test_company_display(self):
        vehicle = {"name": "504", "_org": "XYZ"}
        health = {"coolant_c": 110}
        alerts = ["high_coolant_temp"]
        result = format_health_alert(vehicle, alerts, health, show_company=True)
        assert "XYZ" in result

    def test_truck_name_in_output(self):
        vehicle = {"name": "myTruck", "_org": "CO1"}
        health = {"battery_v": 11.0}
        alerts = ["low_battery"]
        result = format_health_alert(vehicle, alerts, health)
        assert "Truck #myTruck" in result


class TestLowFuelFormatter:
    """format_low_fuel_alert — fuel level below threshold."""

    def test_basic_output(self):
        vehicle = {"name": "601", "_org": "CO1"}
        result = format_low_fuel_alert(vehicle, 12.5)
        assert "Low Fuel" in result
        assert "🟠 WARNING" in result
        assert "Truck #601" in result
        assert "12% fuel remaining" in result

    def test_company_display(self):
        vehicle = {"name": "602", "_org": "ABC"}
        result = format_low_fuel_alert(vehicle, 5.0, show_company=True)
        assert "ABC" in result

    def test_zero_fuel(self):
        vehicle = {"name": "603", "_org": "CO1"}
        result = format_low_fuel_alert(vehicle, 0.0)
        assert "0% fuel remaining" in result
        # 0% trips the critical severity (< 5%) so the critical action
        # wording appears in the 💡 line.
        assert "🔴 CRITICAL" in result
        assert "Refuel immediately" in result

    def test_includes_driver_when_provided(self):
        vehicle = {"name": "604", "_org": "CO1"}
        result = format_low_fuel_alert(
            vehicle, 8.0, driver_name="JANE DOE",
        )
        assert "JANE DOE" in result

    def test_omits_driver_line_when_absent(self):
        vehicle = {"name": "605", "_org": "CO1"}
        result = format_low_fuel_alert(vehicle, 9.0)
        # No "👤" line should render when driver isn't supplied.
        assert "👤" not in result

    def test_includes_location_from_vehicle(self):
        vehicle = {
            "name": "606", "_org": "CO1",
            "location": {"reverseGeo": {"formattedLocation": "Cincinnati, OH"}},
        }
        result = format_low_fuel_alert(vehicle, 10.0)
        assert "Cincinnati, OH" in result

    def test_includes_detection_time(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        vehicle = {"name": "607", "_org": "CO1"}
        result = format_low_fuel_alert(vehicle, 7.0, detected_at=recent)
        assert "min ago" in result


class TestHealthAlertExtras:
    """New driver/location/time fields on format_health_alert."""

    def test_includes_driver_when_provided(self):
        vehicle = {"name": "H1", "_org": "CO1"}
        result = format_health_alert(
            vehicle, ["low_battery"], {"battery_v": 11.0},
            driver_name="JOHN SMITH",
        )
        assert "JOHN SMITH" in result

    def test_includes_location_from_vehicle(self):
        vehicle = {
            "name": "H2", "_org": "CO1",
            "location": {"reverseGeo": {"formattedLocation": "Dayton, OH"}},
        }
        result = format_health_alert(vehicle, ["low_def"], {"def_pct": 5.0})
        assert "Dayton, OH" in result

    def test_includes_detection_time(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        vehicle = {"name": "H3", "_org": "CO1"}
        result = format_health_alert(
            vehicle, ["low_battery"], {"battery_v": 11.5},
            detected_at=recent,
        )
        assert "min ago" in result


class TestFaultAlertExtras:
    """New time field + dropped hint on format_fault_alert."""

    def test_drops_hint_footer(self):
        # The "Tap 🚛 Vehicles for details" / "Immediate attention
        # required" lines used to live at the bottom — they were noise
        # given the inline keyboard already provides the same actions.
        vehicle = {"name": "F1", "location": {}, "_org": "CO1"}
        dtcs = [{"spnDescription": "Engine", "fmiDescription": "Severe",
                 "sourceAddressName": "ECU"}]
        result = format_critical_fault_alert(vehicle, dtcs, {"stopIsOn": True})
        assert "Immediate attention required" not in result
        assert "Tap" not in result

    def test_includes_detection_time(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        vehicle = {"name": "F2", "location": {}, "_org": "CO1"}
        dtcs = [{"spnDescription": "Oil", "fmiDescription": "Warn",
                 "sourceAddressName": "ECU"}]
        # detected_at is on the underlying format_fault_alert, not the
        # backward-compat alias — call it directly.
        from capabilities.formatting import format_fault_alert
        result = format_fault_alert(
            vehicle, dtcs, severity="warning", detected_at=recent,
        )
        assert "min ago" in result


# ══════════════════════════════════════════════════════════════════
# ALERT SETTINGS KEYBOARD
# ══════════════════════════════════════════════════════════════════

class TestAlertSettingsKeyboard:
    """alert_settings_kb — per-type toggle buttons."""

    def _make_user(self, **overrides):
        defaults = dict(
            id=1, telegram_id=100, account_id=1, role=Role.OWNER,
            department="general", truck_num=None, alerts_on=True,
            is_active=True, created_at="2024-01-01",
            alert_faults=True, alert_health=True,
            alert_fuel=True, alert_geofence=True,
        )
        defaults.update(overrides)
        return User(**defaults)

    def test_all_enabled_shows_checkmarks(self):
        user = self._make_user()
        kb = alert_settings_kb(user)
        labels = _all_labels(kb)
        assert any("✅" in l and "Fault" in l for l in labels)
        assert any("✅" in l and "Health" in l for l in labels)
        assert any("✅" in l and "Fuel" in l for l in labels)
        assert any("✅" in l and "Geofence" in l for l in labels)

    def test_disabled_type_shows_cross(self):
        user = self._make_user(alert_health=False, alert_fuel=False)
        kb = alert_settings_kb(user)
        labels = _all_labels(kb)
        assert any("❌" in l and "Health" in l for l in labels)
        assert any("❌" in l and "Fuel" in l for l in labels)
        assert any("✅" in l and "Fault" in l for l in labels)

    def test_has_disable_all_button(self):
        user = self._make_user()
        kb = alert_settings_kb(user)
        callbacks = _all_callbacks(kb)
        assert "alert_disable_all" in callbacks

    def test_has_back_button(self):
        user = self._make_user()
        kb = alert_settings_kb(user)
        callbacks = _all_callbacks(kb)
        assert "cmd_menu" in callbacks

    def test_toggle_callbacks_present(self):
        user = self._make_user()
        kb = alert_settings_kb(user)
        callbacks = _all_callbacks(kb)
        assert "alert_toggle_faults" in callbacks
        assert "alert_toggle_health" in callbacks
        assert "alert_toggle_fuel" in callbacks
        assert "alert_toggle_geofence" in callbacks


# ══════════════════════════════════════════════════════════════════
# COOLANT SPN DETECTION
# ══════════════════════════════════════════════════════════════════

class TestCoolantSPNs:
    """Verify COOLANT_SPNS constant and DTC classification logic."""

    def test_coolant_spns_set(self):
        from capabilities.alerting import COOLANT_SPNS
        assert 110 in COOLANT_SPNS   # coolant temp
        assert 111 in COOLANT_SPNS   # coolant level
        assert 2609 in COOLANT_SPNS  # low coolant level
        assert 441 in COOLANT_SPNS   # coolant pressure
        assert 1691 in COOLANT_SPNS  # coolant additive

    def test_non_coolant_spn_not_in_set(self):
        from capabilities.alerting import COOLANT_SPNS
        assert 100 not in COOLANT_SPNS
        assert 0 not in COOLANT_SPNS


# ══════════════════════════════════════════════════════════════════
# DB MIGRATION
# ══════════════════════════════════════════════════════════════════

class TestAlertPrefMigration:
    """_migrate_alert_prefs adds columns without breaking existing data."""

    @pytest.mark.asyncio
    async def test_migration_idempotent(self, db):
        """Running migration twice does not raise."""
        from adapters.storage.migrations import migrate_alert_prefs
        await migrate_alert_prefs(db._db)
        await migrate_alert_prefs(db._db)  # should be idempotent

    @pytest.mark.asyncio
    async def test_new_user_has_default_prefs(self, seeded_db):
        db, account, _, owner, _, _ = seeded_db
        user = await db.get_user_by_telegram_id(100)
        assert user.alert_faults is True
        assert user.alert_health is True
        assert user.alert_fuel is True
        assert user.alert_geofence is True

    @pytest.mark.asyncio
    async def test_update_alert_pref(self, seeded_db):
        db, _, _, owner, _, _ = seeded_db
        await db.update_user(owner.id, alert_faults=False)
        user = await db.get_user_by_telegram_id(100)
        assert user.alert_faults is False
        assert user.alert_health is True  # unchanged


# ══════════════════════════════════════════════════════════════════
# CRITICAL ALERT DEDUP — DB METHODS
# ══════════════════════════════════════════════════════════════════

class TestCriticalAlertDedup:
    """get_active_vehicle_acks and supersede_alert_ack prevent duplicate alerts."""

    @pytest.mark.asyncio
    async def test_get_active_vehicle_acks_returns_active(self, seeded_db):
        db, account, _, owner, _, _ = seeded_db
        ack_id = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="V1", vehicle_name="Truck 136",
            alert_key="CO:V1:100-4", message_id=111, chat_id=100, sent_to=100,
        )
        result = await db.get_active_vehicle_acks(account.id, "V1", 100)
        assert len(result) == 1
        assert result[0]["id"] == ack_id

    @pytest.mark.asyncio
    async def test_get_active_vehicle_acks_excludes_acked(self, seeded_db):
        db, account, _, owner, _, _ = seeded_db
        ack_id = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="V1", vehicle_name="Truck 136",
            alert_key="CO:V1:100-4", message_id=111, chat_id=100, sent_to=100,
        )
        await db.acknowledge_alert(ack_id, 100)
        result = await db.get_active_vehicle_acks(account.id, "V1", 100)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_supersede_closes_old_ack(self, seeded_db):
        db, account, _, owner, _, _ = seeded_db
        ack1 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="V1", vehicle_name="Truck 136",
            alert_key="CO:V1:100-4", message_id=111, chat_id=100, sent_to=100,
        )
        await db.supersede_alert_ack(ack1)
        # Superseded ack should not appear in active list
        result = await db.get_active_vehicle_acks(account.id, "V1", 100)
        assert len(result) == 0
        # Superseded ack should not appear in unacked query
        pending = await db.get_pending_alerts(account.id)
        assert all(r["id"] != ack1 for r in pending)

    @pytest.mark.asyncio
    async def test_supersede_then_new_ack(self, seeded_db):
        """Old ack superseded, new one created — only new one active."""
        db, account, _, owner, _, _ = seeded_db
        ack1 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="V1", vehicle_name="Truck 136",
            alert_key="CO:V1:100-4", message_id=111, chat_id=100, sent_to=100,
        )
        await db.supersede_alert_ack(ack1)
        ack2 = await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="V1", vehicle_name="Truck 136",
            alert_key="CO:V1:200-3", message_id=222, chat_id=100, sent_to=100,
        )
        result = await db.get_active_vehicle_acks(account.id, "V1", 100)
        assert len(result) == 1
        assert result[0]["id"] == ack2


class TestAutoResolve:
    """Tests for DB auto-resolve and alert system auto-resolve logic."""

    @pytest.fixture
    def seeded_db(self, seeded_db):
        return seeded_db

    async def test_auto_resolve_by_vehicle_resolves_active(self, seeded_db):
        """auto_resolve_alerts_by_vehicle acks all active alerts for vehicle/type."""
        db, account, _, owner, _, _ = seeded_db
        ack1 = await db.create_alert_ack(
            account_id=account.id, alert_type="health",
            vehicle_id="V1", vehicle_name="Truck 100",
            alert_key="CO:V1:low_oil_pressure", message_id=10, chat_id=100, sent_to=100,
        )
        ack2 = await db.create_alert_ack(
            account_id=account.id, alert_type="health",
            vehicle_id="V1", vehicle_name="Truck 100",
            alert_key="CO:V1:low_oil_pressure", message_id=11, chat_id=200, sent_to=200,
        )
        resolved = await db.auto_resolve_alerts_by_vehicle(
            account.id, "health", "V1",
        )
        assert len(resolved) == 2
        # Both should now be acknowledged
        remaining = await db.get_pending_alerts(account.id)
        assert len(remaining) == 0

    async def test_auto_resolve_skips_other_types(self, seeded_db):
        """auto_resolve only resolves matching alert_type."""
        db, account, _, _, _, _ = seeded_db
        await db.create_alert_ack(
            account_id=account.id, alert_type="fault",
            vehicle_id="V1", vehicle_name="Truck 100",
            alert_key="CO:V1:100-4:desc", message_id=10, chat_id=100, sent_to=100,
        )
        resolved = await db.auto_resolve_alerts_by_vehicle(
            account.id, "health", "V1",
        )
        assert len(resolved) == 0
        # Fault alert should still be active
        remaining = await db.get_pending_alerts(account.id)
        assert len(remaining) == 1

    async def test_auto_resolve_skips_already_acked(self, seeded_db):
        """auto_resolve skips already acknowledged alerts."""
        db, account, _, _, _, _ = seeded_db
        ack_id = await db.create_alert_ack(
            account_id=account.id, alert_type="health",
            vehicle_id="V1", vehicle_name="Truck 100",
            alert_key="CO:V1:low_def", message_id=10, chat_id=100, sent_to=100,
        )
        await db.acknowledge_alert(ack_id, user_id=100)
        resolved = await db.auto_resolve_alerts_by_vehicle(
            account.id, "health", "V1",
        )
        assert len(resolved) == 0

    async def test_auto_resolve_returns_records_for_cleanup(self, seeded_db):
        """Resolved records contain message_id/chat_id for message deletion."""
        db, account, _, _, _, _ = seeded_db
        await db.create_alert_ack(
            account_id=account.id, alert_type="fuel",
            vehicle_id="V2", vehicle_name="Truck 200",
            alert_key="CO:V2:fuel:15", message_id=999, chat_id=100, sent_to=100,
        )
        resolved = await db.auto_resolve_alerts_by_vehicle(
            account.id, "fuel", "V2",
        )
        assert len(resolved) == 1
        assert resolved[0]["message_id"] == 999
        assert resolved[0]["chat_id"] == 100
        assert resolved[0]["vehicle_name"] == "Truck 200"


class TestAlertConstants:
    """Verify alert tuning constants are reasonable."""

    def test_warmup_flag_exists(self):
        from capabilities.alerting import _warmup_done
        assert "health" in _warmup_done
        assert "fuel" in _warmup_done
