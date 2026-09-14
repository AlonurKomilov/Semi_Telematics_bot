"""Capacity monitoring's data-retention contribution.

All three tables are PLATFORM scope (one server, no tenant fan-out).
Windows follow the tiering contract: raw minutes exist only to build
the hourly tier (+ the live gauges), so they stay short; the hourly
avg+peak tier is what capacity planning and the headroom trend read,
so it keeps two quarters; per-account usage feeds billing/cost
conversations, so it keeps 13 months (year-over-year comparison).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M"
    )


def _cutoff_day(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


register_target(RetentionTarget(
    "system.metrics_minute", "Capacity samples (raw 60s)", "platform",
    lambda db, _acct, days: db.prune_system_metrics_minute(_cutoff(days)),
))

register_target(RetentionTarget(
    "system.metrics_hourly", "Capacity samples (hourly avg+peak)", "platform",
    lambda db, _acct, days: db.prune_system_metrics_hourly(_cutoff(days)),
))

register_target(RetentionTarget(
    "system.account_usage_daily", "Per-account request metering", "platform",
    lambda db, _acct, days: db.prune_account_usage_daily(_cutoff_day(days)),
))

register_need(RetentionNeed(
    "platform.capacity", "system.metrics_minute", 2,
    "raw tier exists only to build the hourly rollup + live gauges",
))

register_need(RetentionNeed(
    "platform.capacity", "system.metrics_hourly", 180,
    "peak-trend window for capacity planning (two quarters)",
))

register_need(RetentionNeed(
    "platform.capacity", "system.account_usage_daily", 400,
    "per-customer cost history incl. year-over-year comparison",
))

register_target(RetentionTarget(
    "system.usage_breakdown_daily", "Request metering by surface/feature", "platform",
    lambda db, _acct, days: db.prune_usage_breakdown_daily(_cutoff_day(days)),
))

register_need(RetentionNeed(
    "platform.capacity", "system.usage_breakdown_daily", 400,
    "same window as per-account usage — the two answer one question",
))
