"""Centralized scheduled job registration.

All 12 periodic background jobs are registered here instead of inline
in app.py.  Job *implementations* remain in their domain modules.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from interfaces.bot.config import ALERT_INTERVAL


def register_all(scheduler: AsyncIOScheduler, app: Application):
    """Register every periodic background job on *scheduler*."""
    from capabilities.alerting import (
        check_new_faults,
        check_health_alerts,
        check_low_fuel,
        check_camera_alerts,
        deliver_dnd_alerts,
        check_events,
        check_unsafe_parking,
    )
    from interfaces.bot.auto_reports import send_auto_reports
    from interfaces.bot.maintenance import check_overdue_maintenance, check_overdue_by_mileage
    from interfaces.bot.geofences import check_geofence_events

    scheduler.add_job(
        check_new_faults, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fault_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_health_alerts, "interval",
        minutes=15, args=[app], id="health_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_low_fuel, "interval",
        minutes=ALERT_INTERVAL, args=[app], id="fuel_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        send_auto_reports, "interval",
        hours=1, args=[app], id="auto_reports_send",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_overdue_maintenance, "interval",
        hours=24, args=[app], id="maintenance_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_overdue_by_mileage, "interval",
        hours=6, args=[app], id="maintenance_mileage_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_geofence_events, "interval",
        minutes=5, args=[app], id="geofence_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_events, "interval",
        minutes=5, args=[app], id="events_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_camera_alerts, "interval",
        hours=6, args=[app], id="camera_check",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        deliver_dnd_alerts, "cron",
        minute=0, args=[app], id="dnd_delivery",
        max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        check_unsafe_parking, "interval",
        minutes=30, args=[app], id="parking_check",
        max_instances=1, coalesce=True,
    )
