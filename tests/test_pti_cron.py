"""Cron-level tests for the PTI scheduled jobs.

These exercise the integration between ``features.inspections.jobs`` and
the surrounding plumbing — ``is_local_hour`` gating, per-account
fan-out, lazy bot-module imports.  Unit-level behaviour for
``spawn_weekly_inspections`` etc. is already covered in
``test_pti_service.py``; this file focuses on the cron entry points.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from adapters.storage import Role
from features.inspections import jobs as pti_jobs


@pytest.fixture
async def db(pg_db):
    # Wire infra.platform so the cron jobs' get_tenant_db works.
    import infra.platform as _cp
    _old_db = _cp._db
    _cp._db = pg_db
    yield pg_db
    _cp._db = _old_db


# ── Spawn cron ─────────────────────────────────────────────────────


class TestSpawnCron:
    async def test_spawns_at_local_06(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11001, acct.id, role=Role.DRIVER)
        await db.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="107",
            assigned_by=0, is_primary=True,
        )
        await db.update_account(acct.id, timezone="America/New_York")
        # 10:00 UTC = 06:00 ET (EDT — daylight saving June).
        with patch(
            "capabilities.localization.tz.datetime", wraps=datetime,
        ) as mocked:
            mocked.now.return_value = datetime(
                2026, 6, 15, 10, 0, tzinfo=timezone.utc,
            )
            await pti_jobs.job_pti_spawn_weekly()
        ins = await db.get_open_inspection_for_user(driver.id)
        assert ins is not None
        assert ins["vehicle_name"] == "107"

    async def test_skips_outside_local_06(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11002, acct.id, role=Role.DRIVER)
        await db.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="107",
            assigned_by=0, is_primary=True,
        )
        await db.update_account(acct.id, timezone="America/New_York")
        # 14:00 UTC = 10:00 EDT — not 06:00, so spawner should no-op.
        with patch(
            "capabilities.localization.tz.datetime", wraps=datetime,
        ) as mocked:
            mocked.now.return_value = datetime(
                2026, 6, 15, 14, 0, tzinfo=timezone.utc,
            )
            await pti_jobs.job_pti_spawn_weekly()
        ins = await db.get_open_inspection_for_user(driver.id)
        assert ins is None


# ── Digest cron ────────────────────────────────────────────────────


class TestDigestCron:
    async def test_digest_at_local_09(self, db):
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11003, acct.id, role=Role.DRIVER)
        await db.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="107",
            assigned_by=0, is_primary=True,
        )
        await db.update_account(acct.id, timezone="America/New_York")
        # Spawn + submit an inspection so the digest has data to report.
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        ins_id = await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="107",
            template_id=int(tpl["id"]), template_version=int(tpl["version"]),
        )
        await db.add_inspection_items(ins_id, tpl["items"])
        await db.mark_inspection_in_progress(ins_id)
        await db.submit_inspection(ins_id, defects_count=0, has_oos_defect=False)

        # 13:00 UTC = 09:00 EDT — digest should run.
        with patch(
            "capabilities.localization.tz.datetime", wraps=datetime,
        ) as mocked:
            mocked.now.return_value = datetime(
                2026, 6, 15, 13, 0, tzinfo=timezone.utc,
            )
            # Should not raise (lazy import of interfaces.bot.pti is
            # tolerated when the bot module hasn't been wired yet).
            await pti_jobs.job_pti_fleet_digest()


# ── Reminder cron (no local-hour gate) ────────────────────────────


class TestReminderCron:
    async def test_remind_due_soon_picks_up_pending(self, db):
        from datetime import timedelta
        acct = await db.create_account("Fleet")
        driver = await db.create_user(11004, acct.id, role=Role.DRIVER)
        await db.assign_vehicle(
            user_id=driver.id, account_id=acct.id, truck_num="107",
            assigned_by=0, is_primary=True,
        )
        tpl = await db.get_active_template_with_items(acct.id, "truck")
        # Due in 12h — inside the 24h window.
        soon = (
            datetime.now(timezone.utc) + timedelta(hours=12)
        ).isoformat(timespec="seconds")
        await db.create_inspection(
            account_id=acct.id, user_id=driver.id, vehicle_name="107",
            template_id=int(tpl["id"]), template_version=int(tpl["version"]),
            due_by=soon,
        )
        # Should not raise; bot module is optional.
        await pti_jobs.job_pti_remind_due_soon()


# ── Scheduler registration smoke ───────────────────────────────────


class TestSchedulerRegistration:
    def test_jobs_are_importable(self):
        """Confirms the four entry points the scheduler registers can
        be imported by their canonical names — guards against a future
        rename breaking ``interfaces/bot/scheduler.py`` silently."""
        from features.inspections.jobs import (
            job_pti_escalate_overdue,
            job_pti_fleet_digest,
            job_pti_remind_due_soon,
            job_pti_spawn_weekly,
        )
        assert callable(job_pti_spawn_weekly)
        assert callable(job_pti_remind_due_soon)
        assert callable(job_pti_escalate_overdue)
        assert callable(job_pti_fleet_digest)
