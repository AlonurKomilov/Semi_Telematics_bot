"""Telemetry diagnostics API — warehouse status.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
URL history: was GET /admin/warehouse-status until 2026-06-11.
"""
from fastapi import APIRouter, Depends

from interfaces.api.deps import require_permission, get_tenant_db

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


# ── Warehouse diagnostics ─────────────────────────────────────
#
# Surfaces the row counts + freshness of every warehouse table for the
# caller's account. Used during the WAREHOUSE_READS_ENABLED rollout to
# verify that the ingestor is populating the tables before flipping the
# read flag, and afterwards to verify that the data stays fresh. Also
# reports the current value of WAREHOUSE_READS_ENABLED so ops can
# confirm the flag is set correctly per pod.

_WAREHOUSE_TABLES: list[tuple[str, str | None]] = [
    # (table_name, ts_column or None if the table has no timestamp)
    ("vehicle_state",             "fetched_at"),
    ("vehicle_health_snapshot",   "fetched_at"),
    ("vehicle_fault_snapshot",    "fetched_at"),
    ("aggregate_weather_snapshot",    "fetched_at"),
    ("aggregate_efficiency_snapshot", "fetched_at"),
    ("safety_event_log",          "occurred_at"),
    ("driver_efficiency_daily",   "snapshot_date"),
    ("vehicle_telemetry_hourly",  "hour_bucket"),
    ("geofence_definitions",      "fetched_at"),
]


@router.get("/warehouse-status")
async def warehouse_status(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Per-table row count + most-recent-row timestamp for the caller's
    account warehouse. Exposes ``warehouse_reads_enabled`` so ops can
    verify the env var is set on the pod handling the request.

    Designed as the pre-flight check before flipping
    ``WAREHOUSE_READS_ENABLED=1`` in production: if every table has rows
    and the timestamps are within the expected ingestor cadence (60s /
    5min / hourly), the flag is safe to flip.
    """
    from infra import config as _cfg

    account_id = user["account_id"]
    tables: list[dict] = []

    for name, ts_col in _WAREHOUSE_TABLES:
        entry: dict = {"table": name}
        try:
            count_row = await tenant.read_one(
                f"SELECT COUNT(*) AS n FROM {name} WHERE account_id = ?",
                (account_id,),
            )
            entry["rows"] = int(count_row["n"]) if count_row else 0
        except Exception as e:
            # Table may not exist yet on a freshly-installed tenant DB
            # that hasn't run the warehouse migrations. Surface it as a
            # diagnostic rather than failing the whole endpoint.
            entry["rows"] = 0
            entry["error"] = str(e)[:200]
            tables.append(entry)
            continue

        if ts_col and entry["rows"] > 0:
            try:
                ts_row = await tenant.read_one(
                    f"SELECT MAX({ts_col}) AS ts FROM {name} "
                    f"WHERE account_id = ?",
                    (account_id,),
                )
                entry["last_seen"] = ts_row["ts"] if ts_row else None
            except Exception:
                entry["last_seen"] = None

        tables.append(entry)

    populated = sum(1 for t in tables if t.get("rows", 0) > 0)
    return {
        "account_id": account_id,
        "warehouse_reads_enabled": bool(getattr(_cfg, "WAREHOUSE_READS_ENABLED", False)),
        "tables": tables,
        "summary": {
            "total":     len(tables),
            "populated": populated,
            "empty":     len(tables) - populated,
        },
    }


