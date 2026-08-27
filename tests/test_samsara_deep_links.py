"""Samsara deep links — adapters.samsara URL construction.

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


class TestSamsaraDeepLinks:
    """Tests for samsara_vehicle_url helper and alert keyboard URL button."""

    def test_url_builder_fault(self):
        from adapters.samsara.client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "fault",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_health(self):
        from adapters.samsara.client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "health",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_fuel(self):
        from adapters.samsara.client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "fuel",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_events(self):
        from adapters.samsara.client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "events",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_unknown_type_fallback(self):
        from adapters.samsara.client import samsara_vehicle_url
        url = samsara_vehicle_url("12345", "v999", "unknown_type",
                                  dashboard_base="https://cloud.samsara.com")
        assert url == "https://cloud.samsara.com/o/12345/devices/v999/vehicle"

    def test_url_builder_no_org_returns_none(self):
        from adapters.samsara.client import samsara_vehicle_url
        assert samsara_vehicle_url("", "v999", "fault") is None
        assert samsara_vehicle_url("", "v999", "health") is None

    def test_keyboard_includes_url_button_when_org_known(self):
        from capabilities.alerting import build_alert_button_specs, AlertSeverity
        import adapters.samsara.client as samsara_client
        samsara_client.ORG_IDS["TEST"] = "org123"
        try:
            kb = build_alert_button_specs(
                AlertSeverity.CRITICAL, "TEST", "T100",
                ack_id=1, alert_type="fault", vehicle_id="v555",
            )
            urls = [b.get("url") for r in kb for b in r if b.get("url")]
            assert any("org123" in u and "v555" in u for u in urls)
            labels = [b["text"] for r in kb for b in r]
            assert any("Samsara" in lbl for lbl in labels)
        finally:
            samsara_client.ORG_IDS.pop("TEST", None)

    def test_keyboard_no_url_button_when_org_unknown(self):
        from capabilities.alerting import build_alert_button_specs, AlertSeverity
        import adapters.samsara.client as samsara_client
        samsara_client.ORG_IDS.pop("NOPE", None)
        kb = build_alert_button_specs(
            AlertSeverity.CRITICAL, "NOPE", "T100",
            ack_id=1, alert_type="fault", vehicle_id="v555",
        )
        urls = [b.get("url") for r in kb for b in r if b.get("url")]
        assert len(urls) == 0

    def test_keyboard_info_severity_with_url(self):
        from capabilities.alerting import build_alert_button_specs, AlertSeverity
        import adapters.samsara.client as samsara_client
        samsara_client.ORG_IDS["CO2"] = "org456"
        try:
            kb = build_alert_button_specs(
                AlertSeverity.INFO, "CO2", "T200",
                alert_type="events", vehicle_id="v777",
            )
            urls = [b.get("url") for r in kb for b in r if b.get("url")]
            assert len(urls) == 1
            assert "/devices/" in urls[0] and "/vehicle" in urls[0]
            labels = [b["text"] for r in kb for b in r]
            # INFO → no ACK, no AI Diagnose, but should have URL + View Truck + Menu
            assert "✅ Acknowledge" not in labels
            assert "🤖 AI Diagnose" not in labels
        finally:
            samsara_client.ORG_IDS.pop("CO2", None)

    def test_org_ids_dict_exists(self):
        from adapters.samsara.client import ORG_IDS
        assert isinstance(ORG_IDS, dict)

    def test_dashboard_url_config(self):
        from interfaces.bot.config import SAMSARA_DASHBOARD_URL
        assert "cloud" in SAMSARA_DASHBOARD_URL
        assert "samsara.com" in SAMSARA_DASHBOARD_URL
