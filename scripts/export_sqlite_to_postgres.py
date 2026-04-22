#!/usr/bin/env python3
"""Export SQLite databases to PostgreSQL.

Usage:
    python3 scripts/export_sqlite_to_postgres.py \\
        --pg-url postgresql://user:pass@localhost/semi_telematics \\
        [--sqlite-platform data/platform.db] \\
        [--sqlite-tenant data/tenants/tenant_1.db] \\
        [--tenant-id 1] \\
        [--dry-run]

What it does:
  1. Connects to the target PostgreSQL database (must already exist, schema
     will be created automatically by the app on first start).
  2. Reads every row from each SQLite table.
  3. Inserts rows into PostgreSQL using COPY (fastest) or INSERT ON CONFLICT DO NOTHING.
  4. Updates sequences to avoid PK collisions (PostgreSQL uses sequences for SERIAL).

Run with --dry-run first to see what would be exported without writing anything.

Prerequisites:
    pip install asyncpg aiosqlite
    createdb semi_telematics   # or use your managed PG instance

Migration workflow:
  1. Stop all app services (make stop).
  2. Run this script with --dry-run — verify output.
  3. Run without --dry-run.
  4. Set DATABASE_URL=postgresql://... in .env
  5. Start services (make start) — app runs schema/migrations on first connect.
  6. Verify data via /api/health and spot-check a few accounts.
  7. Keep SQLite files as cold backup for 30 days.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Table definitions ────────────────────────────────────────────────
# Tables in dependency order (FK targets before referencing tables).
# Each entry: (table_name, primary_key_col or None)

PLATFORM_TABLES = [
    ("accounts",              "id"),
    ("users",                 "id"),
    ("invites",               "id"),
    ("authorized_chats",      "id"),
    ("ai_usage",              "id"),
    ("knowledge_base",        "id"),
    ("role_permissions",      "id"),
    ("driver_trucks",         "id"),
    ("user_companies",        "id"),
    ("subscriptions",         "id"),
    ("billing_usage_snapshots","id"),
]

TENANT_TABLES = [
    ("companies",             "id"),
    ("settings",              "id"),
    ("fault_alerts",          "id"),
    ("health_alerts",         "id"),
    ("fuel_alerts",           "id"),
    ("geofence_alerts",       "id"),
    ("parking_spots",         "id"),
    ("parking_events",        "id"),
    ("camera_events",         "id"),
    ("maintenance_records",   "id"),
    ("fuel_records",          "id"),
    ("schedules",             "id"),
]


# ── SQLite reader ────────────────────────────────────────────────────

async def read_sqlite_table(sqlite_path: str, table: str) -> tuple[list[str], list[tuple]]:
    """Read all rows from a SQLite table.  Returns (columns, rows)."""
    import aiosqlite
    async with aiosqlite.connect(sqlite_path) as conn:
        conn.row_factory = aiosqlite.Row
        # Check table exists
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if not await cur.fetchone():
            return [], []

        cur = await conn.execute(f"SELECT * FROM {table}")
        rows = await cur.fetchall()
        if not rows:
            return [], []
        cols = list(rows[0].keys())
        data = [tuple(row[c] for c in cols) for row in rows]
        return cols, data


# ── PostgreSQL writer ────────────────────────────────────────────────

def _sqlite_type_to_pg(value):
    """Convert SQLite value to a type asyncpg can handle."""
    if isinstance(value, bytes):
        return value
    return value


async def insert_pg_rows(
    pg_conn,
    table: str,
    cols: list[str],
    rows: list[tuple],
    pk_col: str | None,
    dry_run: bool,
) -> int:
    """Insert rows into a PostgreSQL table.  Returns count inserted."""
    if not rows:
        return 0

    col_list = ", ".join(cols)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )

    if dry_run:
        logger.info("  [DRY RUN] Would insert %d rows into %s", len(rows), table)
        return len(rows)

    count = 0
    for row in rows:
        typed = tuple(_sqlite_type_to_pg(v) for v in row)
        try:
            result = await pg_conn.execute(sql, *typed)
            if result and result.split()[-1] != "0":
                count += 1
        except Exception as e:
            logger.warning("  Skipped row in %s: %s — %s", table, row[:3], e)

    return count


async def reset_pg_sequence(pg_conn, table: str, pk_col: str, dry_run: bool) -> None:
    """Reset the PostgreSQL sequence for a SERIAL column to max(id)+1."""
    row = await pg_conn.fetchrow(f"SELECT MAX({pk_col}) FROM {table}")
    max_id = row[0] if row and row[0] else 0
    seq_sql = f"SELECT setval(pg_get_serial_sequence('{table}', '{pk_col}'), {max_id + 1}, false)"
    if dry_run:
        logger.info("  [DRY RUN] Would reset sequence for %s.%s to %d", table, pk_col, max_id + 1)
    else:
        await pg_conn.execute(seq_sql)


# ── Main migration logic ─────────────────────────────────────────────

async def migrate_database(
    pg_url: str,
    sqlite_path: str,
    table_defs: list[tuple[str, str | None]],
    label: str,
    dry_run: bool,
) -> None:
    import asyncpg
    logger.info("=== Migrating %s: %s → PostgreSQL ===", label, sqlite_path)

    pg_conn = await asyncpg.connect(pg_url)
    total_inserted = 0

    try:
        for table, pk_col in table_defs:
            cols, rows = await read_sqlite_table(sqlite_path, table)
            if not cols:
                logger.info("  %s — table empty or missing, skipping", table)
                continue

            logger.info("  %s — %d rows", table, len(rows))
            inserted = await insert_pg_rows(pg_conn, table, cols, rows, pk_col, dry_run)
            total_inserted += inserted

            if pk_col and not dry_run:
                await reset_pg_sequence(pg_conn, table, pk_col, dry_run)

    finally:
        await pg_conn.close()

    logger.info("=== %s done: %d rows written ===", label, total_inserted)


async def main(args: argparse.Namespace) -> None:
    pg_url = args.pg_url

    if args.dry_run:
        logger.info("*** DRY RUN — no data will be written to PostgreSQL ***")

    if args.sqlite_platform:
        if not os.path.exists(args.sqlite_platform):
            logger.error("Platform SQLite not found: %s", args.sqlite_platform)
            sys.exit(1)
        await migrate_database(
            pg_url, args.sqlite_platform,
            PLATFORM_TABLES, "platform DB",
            args.dry_run,
        )

    if args.sqlite_tenant:
        if not os.path.exists(args.sqlite_tenant):
            logger.error("Tenant SQLite not found: %s", args.sqlite_tenant)
            sys.exit(1)
        label = f"tenant {args.tenant_id or 'unknown'}"
        await migrate_database(
            pg_url, args.sqlite_tenant,
            TENANT_TABLES, label,
            args.dry_run,
        )

    if args.all_tenants:
        tenant_dir = args.all_tenants
        if not os.path.isdir(tenant_dir):
            logger.error("Tenant directory not found: %s", tenant_dir)
            sys.exit(1)
        for fname in sorted(os.listdir(tenant_dir)):
            if not fname.endswith(".db"):
                continue
            path = os.path.join(tenant_dir, fname)
            label = f"tenant {fname}"
            await migrate_database(
                pg_url, path, TENANT_TABLES, label, args.dry_run,
            )

    if args.dry_run:
        logger.info("*** DRY RUN complete — run without --dry-run to write ***")
    else:
        logger.info("Migration complete.")
        logger.info(
            "Next steps:\n"
            "  1. Set DATABASE_URL=%s in .env\n"
            "  2. make restart\n"
            "  3. curl http://localhost:8000/api/health   # should return ok",
            pg_url,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export SQLite → PostgreSQL")
    parser.add_argument("--pg-url", required=True,
                        help="PostgreSQL connection URL (postgresql://user:pass@host/db)")
    parser.add_argument("--sqlite-platform",
                        help="Path to platform SQLite DB (e.g. data/platform.db)")
    parser.add_argument("--sqlite-tenant",
                        help="Path to a single tenant SQLite DB")
    parser.add_argument("--tenant-id", type=int,
                        help="Account ID for the single tenant DB (for logging)")
    parser.add_argument("--all-tenants",
                        help="Directory containing all tenant .db files (e.g. data/tenants/)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be migrated without writing")
    args = parser.parse_args()

    if not args.sqlite_platform and not args.sqlite_tenant and not args.all_tenants:
        parser.error("Specify at least one of: --sqlite-platform, --sqlite-tenant, --all-tenants")

    asyncio.run(main(args))
