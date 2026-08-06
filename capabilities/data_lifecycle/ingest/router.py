"""Telemetry diagnostics API — warehouse status.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.
URL history: was GET /admin/warehouse-status until 2026-06-11.
"""
from fastapi import APIRouter, Depends

from interfaces.api.deps import require_permission, get_tenant_db

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("/warehouse-status")
async def warehouse_status(
    user: dict = Depends(require_permission("can_manage_account")),
    tenant=Depends(get_tenant_db),
):
    """Registry-driven warehouse health for the caller's account.

    The old version hand-listed tables with timestamp columns that no
    longer existed — blind for months without erroring.  This one asks
    the SSOT instead: every registered ingest dataset reports its
    tables (row counts + newest ``source_ts``), its staleness verdict
    against its OWN declared SLA (Contract 2: unknown age IS stale),
    and its ingest-ledger line.  The BUILD tiers and the bot pulse
    ride along.  A new dataset appears here the day it is registered —
    nothing to update by hand ever again.
    """
    from infra import config as _cfg
    from capabilities.data_lifecycle.ingest import all_datasets, discover
    from capabilities.data_lifecycle.staleness import (
        data_age_minutes,
        freshest,
        is_stale,
    )

    discover()
    account_id = user["account_id"]

    datasets: list[dict] = []
    for d in sorted(all_datasets(), key=lambda x: x.key):
        entry: dict = {
            "dataset": d.key,
            "owner": d.owner,
            "sla_min": d.freshness_sla_min,
            "expect_rows": d.expect_rows,
            "tables": [],
        }
        newest = None
        for table in d.tables:
            try:
                row = await tenant.read_one(
                    f"SELECT COUNT(*) AS n, MAX(source_ts) AS ts "
                    f"FROM {table} WHERE account_id = ?",
                    (account_id,),
                )
                entry["tables"].append({
                    "table": table,
                    "rows": int(row["n"] or 0) if row else 0,
                    "newest_source_ts": row["ts"] if row else None,
                })
                if row and row["ts"]:
                    newest = freshest(newest, row["ts"])
            except Exception as e:
                entry["tables"].append(
                    {"table": table, "rows": 0, "error": str(e)[:200]}
                )
        age = data_age_minutes(newest)
        entry["age_min"] = round(age, 1) if age is not None else None
        entry["stale"] = is_stale(newest, d.freshness_sla_min)
        try:
            led = await tenant.read_one(
                "SELECT day, runs, rows_sum, last_rows, last_ran_at "
                "FROM ingest_runs WHERE account_id = ? AND dataset_key = ? "
                "ORDER BY day DESC LIMIT 1",
                (account_id, d.key),
            )
            if led:
                entry["ledger"] = dict(led)
        except Exception:
            pass
        datasets.append(entry)

    # BUILD side — the aggregate tiers, newest bucket per grain.
    tiers: list[dict] = []
    for granularity, grain in (("hourly", "hour"), ("daily", "day"),
                               ("weekly", "week")):
        try:
            row = await tenant.read_one(
                f"SELECT COUNT(*) AS n, MAX(bucket_start) AS newest "
                f"FROM warehouse.vehicle_state_{grain} "
                f"WHERE account_id = ?",
                (account_id,),
            )
            tiers.append({
                "grain": grain,
                "rows": int(row["n"] or 0) if row else 0,
                "newest_bucket": row["newest"] if row else None,
            })
        except Exception as e:
            tiers.append({"grain": grain, "rows": 0, "error": str(e)[:200]})

    # The bot process pulse (cross-process heartbeat — same source as
    # /health's "bot" field).
    try:
        from capabilities.platform.capacity.sampler import bot_heartbeat_age_min
        pulse = await bot_heartbeat_age_min(tenant)
        bot = {"age_min": round(pulse, 1) if pulse is not None else None,
               "status": "ok" if pulse is not None and pulse <= 3 else
                         ("silent" if pulse is not None else "unknown")}
    except Exception:
        bot = {"age_min": None, "status": "unknown"}

    stale_count = sum(1 for d in datasets if d["stale"])
    return {
        "account_id": account_id,
        "warehouse_reads_enabled": bool(
            getattr(_cfg, "WAREHOUSE_READS_ENABLED", False)
        ),
        "bot": bot,
        "datasets": datasets,
        "build_tiers": tiers,
        "summary": {
            "datasets": len(datasets),
            "stale": stale_count,
            "fresh": len(datasets) - stale_count,
        },
    }
