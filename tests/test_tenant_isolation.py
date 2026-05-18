"""Cross-tenant isolation tests.

Verify that account_id scoping on DB methods prevents one tenant from
reading, updating, or deleting another tenant's resources.
"""

from __future__ import annotations

import pytest_asyncio

from adapters.storage import Database, Role


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def two_tenants(db: Database):
    """Set up two independent accounts (tenants), each with a company, user,
    a maintenance task, a work schedule, an alert ack, and a parking event."""

    # Tenant A
    acct_a = await db.create_account("Tenant A")
    comp_a = await db.add_company(acct_a.id, "COMPA", "key_a", "Company A")
    user_a = await db.create_user(telegram_id=1001, account_id=acct_a.id, role=Role.OWNER)
    task_a = await db.add_maintenance_task(
        account_id=acct_a.id, company_code="COMPA",
        vehicle_name="Truck-A1", task_type="oil_change",
        description="Oil change for A",
    )
    sched_a = await db.create_work_hour(
        account_id=acct_a.id, label="Day shift A",
        start_hour=6, end_hour=18, created_by=1001,
    )
    alert_a = await db.create_alert_ack(
        account_id=acct_a.id, alert_type="fault",
        vehicle_id="v_a1", vehicle_name="Truck-A1",
        alert_key="fault:v_a1:p0123", message_id=100, chat_id=200, sent_to=1001,
    )
    parking_a = await db.upsert_parking_event(
        account_id=acct_a.id, vehicle_id="v_a1",
        vehicle_name="Truck-A1", company_code="COMPA",
        latitude=40.0, longitude=-74.0, address="123 Main St",
        first_stopped="2025-01-01T00:00:00", duration_hours=2.0,
        location_class="street",
    )

    # Tenant B
    acct_b = await db.create_account("Tenant B")
    comp_b = await db.add_company(acct_b.id, "COMPB", "key_b", "Company B")
    user_b = await db.create_user(telegram_id=2001, account_id=acct_b.id, role=Role.OWNER)
    task_b = await db.add_maintenance_task(
        account_id=acct_b.id, company_code="COMPB",
        vehicle_name="Truck-B1", task_type="tire_rotation",
        description="Tires for B",
    )
    sched_b = await db.create_work_hour(
        account_id=acct_b.id, label="Night shift B",
        start_hour=18, end_hour=6, created_by=2001,
    )
    alert_b = await db.create_alert_ack(
        account_id=acct_b.id, alert_type="health",
        vehicle_id="v_b1", vehicle_name="Truck-B1",
        alert_key="health:v_b1:eng", message_id=300, chat_id=400, sent_to=2001,
    )
    parking_b = await db.upsert_parking_event(
        account_id=acct_b.id, vehicle_id="v_b1",
        vehicle_name="Truck-B1", company_code="COMPB",
        latitude=41.0, longitude=-75.0, address="456 Oak Ave",
        first_stopped="2025-01-01T01:00:00", duration_hours=3.0,
        location_class="lot",
    )

    return {
        "db": db,
        "a": {
            "account": acct_a, "company": comp_a, "user": user_a,
            "task_id": task_a, "schedule": sched_a,
            "alert_id": alert_a, "parking": parking_a,
        },
        "b": {
            "account": acct_b, "company": comp_b, "user": user_b,
            "task_id": task_b, "schedule": sched_b,
            "alert_id": alert_b, "parking": parking_b,
        },
    }


# ---------------------------------------------------------------------------
# Company isolation
# ---------------------------------------------------------------------------

class TestCompanyIsolation:
    async def test_update_company_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.update_company(
            d["a"]["company"].id,
            account_id=d["b"]["account"].id,
            display_name="Hacked",
        )
        assert ok is False

    async def test_remove_company_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.remove_company(
            d["a"]["company"].id,
            account_id=d["b"]["account"].id,
        )
        assert ok is False

    async def test_update_company_own_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.update_company(
            d["a"]["company"].id,
            account_id=d["a"]["account"].id,
            display_name="Updated A",
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Schedule isolation
# ---------------------------------------------------------------------------

class TestScheduleIsolation:
    async def test_get_schedule_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        result = await db.get_work_hour(
            d["a"]["schedule"]["id"],
            account_id=d["b"]["account"].id,
        )
        assert result is None

    async def test_update_schedule_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.update_work_hour(
            d["a"]["schedule"]["id"],
            account_id=d["b"]["account"].id,
            label="Hacked schedule",
        )
        assert ok is False

    async def test_delete_schedule_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        # Delete with wrong account — should silently not delete
        await db.delete_work_hour(
            d["a"]["schedule"]["id"],
            account_id=d["b"]["account"].id,
        )
        # Verify it still exists
        sched = await db.get_work_hour(
            d["a"]["schedule"]["id"],
            account_id=d["a"]["account"].id,
        )
        assert sched is not None


# ---------------------------------------------------------------------------
# Maintenance isolation
# ---------------------------------------------------------------------------

class TestMaintenanceIsolation:
    async def test_get_task_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        result = await db.get_maintenance_task(
            d["a"]["task_id"],
            account_id=d["b"]["account"].id,
        )
        assert result is None

    async def test_update_task_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.update_maintenance_task(
            d["a"]["task_id"],
            account_id=d["b"]["account"].id,
            description="Hacked task",
        )
        assert ok is False

    async def test_update_status_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.update_maintenance_status(
            d["a"]["task_id"],
            "completed",
            account_id=d["b"]["account"].id,
        )
        assert ok is False

    async def test_delete_task_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        await db.delete_maintenance_task(
            d["a"]["task_id"],
            account_id=d["b"]["account"].id,
        )
        # Still exists for the real owner
        task = await db.get_maintenance_task(
            d["a"]["task_id"],
            account_id=d["a"]["account"].id,
        )
        assert task is not None


# ---------------------------------------------------------------------------
# Alert isolation
# ---------------------------------------------------------------------------

class TestAlertIsolation:
    async def test_get_alert_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        result = await db.get_alert_ack_by_id(
            d["a"]["alert_id"],
            account_id=d["b"]["account"].id,
        )
        assert result is None

    async def test_acknowledge_alert_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.acknowledge_alert(
            d["a"]["alert_id"],
            user_id=d["b"]["user"].telegram_id,
            account_id=d["b"]["account"].id,
        )
        assert ok is False

    async def test_supersede_alert_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        await db.supersede_alert_ack(
            d["a"]["alert_id"],
            account_id=d["b"]["account"].id,
        )
        # Original should still be active (not superseded)
        alert = await db.get_alert_ack_by_id(
            d["a"]["alert_id"],
            account_id=d["a"]["account"].id,
        )
        assert alert is not None
        assert alert.get("status") != "superseded"

    async def test_acknowledge_alert_own_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.acknowledge_alert(
            d["a"]["alert_id"],
            user_id=d["a"]["user"].telegram_id,
            account_id=d["a"]["account"].id,
        )
        assert ok is True


# ---------------------------------------------------------------------------
# Parking isolation
# ---------------------------------------------------------------------------

class TestParkingIsolation:
    async def test_get_parking_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        result = await db.get_parking_event_by_id(
            d["a"]["parking"]["id"],
            account_id=d["b"]["account"].id,
        )
        assert result is None

    async def test_update_parking_wrong_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        ok = await db.update_parking_alert_level(
            d["a"]["parking"]["id"],
            alert_level="high",
            ai_analysis="suspicious",
            account_id=d["b"]["account"].id,
        )
        assert ok is False

    async def test_get_parking_own_tenant(self, two_tenants):
        d = two_tenants
        db = d["db"]
        result = await db.get_parking_event_by_id(
            d["a"]["parking"]["id"],
            account_id=d["a"]["account"].id,
        )
        assert result is not None
        assert result["vehicle_name"] == "Truck-A1"
