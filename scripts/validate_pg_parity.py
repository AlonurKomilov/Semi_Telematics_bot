#!/usr/bin/env python3
"""Compare row counts SQLite vs PostgreSQL — read-only validation tool.

Run during the dual-write phase (or after a backfill) to verify that
every table on the SQLite primary is mirrored on the PG secondary.
Read-only: never writes to either database.

Usage:
    python3 scripts/validate_pg_parity.py \\
        --pg-url postgresql://user:pass@localhost/4truck \\
        [--sqlite-platform data/bot.db] \\
        [--sqlite-tenant data/tenants/tenant_1.db --tenant-id 1]
        [--tolerance 5]    # rows can drift up to N before flagging
        [--json]           # emit machine-readable report

Exit codes:
    0 — all tables within tolerance
    1 — at least one table diverged
    2 — connection / config error

Reports per-table:
    table          sqlite      pg          diff   status
    accounts       12          12          0      OK
    users          487         487         0      OK
    score_events   12480       12473       -7     DRIFT  (within tolerance)
    audit_log      9213        0           -9213  MISSING

The SQLite-to-PG export script (export_sqlite_to_postgres.py) is the
companion that actually moves data; this script verifies the move
worked. Re-running validate after a top-up backfill is the standard
loop during the dual-write phase.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Reuse the table list we ship with the migration script so this tool
# never falls behind on schema changes.
from scripts.export_sqlite_to_postgres import PLATFORM_TABLES, TENANT_TABLES  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger("pg_parity")


# ── SQLite reader ───────────────────────────────────────────────────

async def _sqlite_count(sqlite_path: str, table: str) -> int:
    import aiosqlite
    async with aiosqlite.connect(sqlite_path) as conn:
        try:
            cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cur.fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return -1  # table doesn't exist on this side


# ── PG reader ───────────────────────────────────────────────────────

async def _pg_count(pg_conn, table: str, schema: str | None = None) -> int:
    import asyncpg
    qualified = f'"{schema}"."{table}"' if schema else f'"{table}"'
    try:
        return int(await pg_conn.fetchval(f"SELECT COUNT(*) FROM {qualified}"))
    except asyncpg.UndefinedTableError:
        return -1
    except Exception:
        return -1


# ── Comparison loop ─────────────────────────────────────────────────

async def compare_tables(
    sqlite_path: str,
    pg_url: str,
    tables: list[tuple[str, str | None]],
    *,
    pg_schema: str | None = None,
    tolerance: int = 0,
) -> list[dict]:
    """Returns a list of per-table dicts:
       {table, sqlite, pg, diff, status}
    where status is 'OK' | 'DRIFT' | 'MISSING' | 'EXTRA' | 'ABSENT'."""
    import asyncpg
    pg_conn = await asyncpg.connect(pg_url)
    try:
        out: list[dict] = []
        for table, _pk in tables:
            sq = await _sqlite_count(sqlite_path, table)
            pg = await _pg_count(pg_conn, table, schema=pg_schema)
            if sq < 0 and pg < 0:
                status = "ABSENT"
            elif sq < 0:
                status = "EXTRA"   # table only in PG
            elif pg < 0:
                status = "MISSING" # table only in SQLite
            elif sq == pg:
                status = "OK"
            elif abs(sq - pg) <= tolerance:
                status = "DRIFT_OK"
            else:
                status = "DRIFT"
            out.append({
                "table":  table,
                "sqlite": sq,
                "pg":     pg,
                "diff":   (pg - sq) if (sq >= 0 and pg >= 0) else None,
                "status": status,
            })
        return out
    finally:
        await pg_conn.close()


# ── CLI ─────────────────────────────────────────────────────────────

def _print_report(rows: list[dict]) -> int:
    """Pretty-print the report. Returns the count of NON-OK rows."""
    print(f"{'TABLE':35} {'SQLITE':>10} {'PG':>10} {'DIFF':>8}  STATUS")
    print("-" * 80)
    bad = 0
    for r in rows:
        sq = "—" if r["sqlite"] < 0 else str(r["sqlite"])
        pg = "—" if r["pg"] < 0 else str(r["pg"])
        df = "—" if r["diff"] is None else (f"+{r['diff']}" if r['diff'] > 0 else str(r['diff']))
        status = r["status"]
        print(f"{r['table']:35} {sq:>10} {pg:>10} {df:>8}  {status}")
        if status not in ("OK", "DRIFT_OK", "ABSENT"):
            bad += 1
    print("-" * 80)
    print(f"Diverged tables: {bad}/{len(rows)}")
    return bad


async def main(args: argparse.Namespace) -> int:
    if not args.pg_url:
        logger.error("--pg-url is required")
        return 2

    overall_bad = 0

    # Platform DB
    if args.sqlite_platform:
        logger.info("Validating PLATFORM DB (%s)", args.sqlite_platform)
        rows = await compare_tables(
            args.sqlite_platform, args.pg_url, PLATFORM_TABLES,
            pg_schema=args.pg_platform_schema,
            tolerance=args.tolerance,
        )
        if args.json:
            print(json.dumps({"platform": rows}, indent=2))
        else:
            overall_bad += _print_report(rows)

    # Tenant DB
    if args.sqlite_tenant:
        if args.tenant_id is None:
            logger.error("--tenant-id is required when --sqlite-tenant is set")
            return 2
        logger.info("Validating TENANT %d DB (%s)", args.tenant_id, args.sqlite_tenant)
        rows = await compare_tables(
            args.sqlite_tenant, args.pg_url, TENANT_TABLES,
            pg_schema=args.pg_tenant_schema or f"tenant_{args.tenant_id}",
            tolerance=args.tolerance,
        )
        if args.json:
            print(json.dumps({"tenant": rows}, indent=2))
        else:
            overall_bad += _print_report(rows)

    return 1 if overall_bad else 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pg-url", required=True,
                   help="postgresql://user:pass@host/db")
    p.add_argument("--sqlite-platform",
                   help="Path to platform SQLite DB (e.g. data/bot.db)")
    p.add_argument("--sqlite-tenant",
                   help="Path to one tenant SQLite DB")
    p.add_argument("--tenant-id", type=int,
                   help="Tenant id (used to build pg schema name tenant_<id>)")
    p.add_argument("--pg-platform-schema", default=None,
                   help="Override platform schema (default: public)")
    p.add_argument("--pg-tenant-schema", default=None,
                   help="Override tenant schema (default: tenant_<id>)")
    p.add_argument("--tolerance", type=int, default=0,
                   help="Allowable row-count drift before flagging (default 0)")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON report instead of human-readable table")
    sys.exit(asyncio.run(main(p.parse_args())))
