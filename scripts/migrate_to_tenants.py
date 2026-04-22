#!/usr/bin/env python3
"""Migrate single bot.db → platform.db + per-tenant SQLite databases.

Usage:
    python3 scripts/migrate_to_tenants.py [--db data/bot.db] [--out data/]

Steps:
    1. Read all data from the existing bot.db
    2. Create data/platform.db with platform tables (accounts, users, invites, chats, ai_usage)
    3. For each account, create data/tenants/tenant_N.db with tenant tables
    4. Copy rows with account_id matching
    5. Verify row counts
    6. Create backup of original bot.db
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from adapters.storage import platform_schema, tenant_schema

logger = logging.getLogger(__name__)

# Tables that go into platform.db
PLATFORM_TABLES = ["accounts", "users", "invites", "authorized_chats", "ai_usage"]

# Tables that go into per-tenant DBs
TENANT_TABLES = [
    "companies", "maintenance_tasks", "fuel_entries", "account_settings",
    "alert_acknowledgments", "alert_history", "dnd_alert_queue",
    "audit_log", "camera_checks", "parking_events", "work_schedules",
]

# Tables routed to tenant via a JOIN (no direct account_id column)
TENANT_TABLES_VIA_JOIN = {
    "digest_subscriptions": {
        "join": "JOIN users ON digest_subscriptions.user_id = users.id",
        "where": "users.account_id = ?",
        "count_where": "user_id IN (SELECT id FROM users WHERE account_id = ?)",
    },
}


async def get_table_columns(conn: aiosqlite.Connection, table: str) -> list[str]:
    """Get column names for a table."""
    cur = await conn.execute(f"PRAGMA table_info({table})")
    rows = await cur.fetchall()
    return [r[1] for r in rows]


async def table_exists(conn: aiosqlite.Connection, table: str) -> bool:
    """Check if a table exists."""
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return await cur.fetchone() is not None


async def copy_table(
    src: aiosqlite.Connection,
    dst: aiosqlite.Connection,
    table: str,
    where: str = "",
    params: tuple = (),
    join: str = "",
) -> int:
    """Copy rows from src to dst for a table. Returns row count."""
    # Get columns that exist in both source and destination
    src_cols = set(await get_table_columns(src, table))
    dst_cols = await get_table_columns(dst, table)
    common_cols = [c for c in dst_cols if c in src_cols]

    if not common_cols:
        logger.warning(f"  No common columns for {table}, skipping")
        return 0

    insert_cols = ", ".join(common_cols)
    select_cols = ", ".join(f"{table}.{c}" for c in common_cols) if join else insert_cols
    placeholders = ", ".join("?" for _ in common_cols)

    query = f"SELECT {select_cols} FROM {table}"
    if join:
        query += f" {join}"
    if where:
        query += f" WHERE {where}"

    cur = await src.execute(query, params)
    rows = await cur.fetchall()

    if not rows:
        return 0

    await dst.executemany(
        f"INSERT OR IGNORE INTO {table} ({insert_cols}) VALUES ({placeholders})",
        [tuple(r) for r in rows],
    )
    await dst.commit()
    return len(rows)


async def migrate(db_path: str, out_dir: str, *, dry_run: bool = False) -> dict:
    """Run the migration.

    Returns a summary dict with counts.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Source database not found: {db_path}")

    platform_path = os.path.join(out_dir, "platform.db")
    tenants_dir = os.path.join(out_dir, "tenants")

    if os.path.exists(platform_path):
        raise FileExistsError(
            f"platform.db already exists at {platform_path}. "
            "Remove it first or use a different output directory."
        )

    # Backup original
    backup_path = db_path + ".backup"
    if not os.path.exists(backup_path):
        shutil.copy2(db_path, backup_path)
        logger.info(f"Backed up {db_path} → {backup_path}")
    else:
        logger.info(f"Backup already exists: {backup_path}")

    summary = {"platform": {}, "tenants": {}}

    # Open source
    src = await aiosqlite.connect(db_path)
    src.row_factory = aiosqlite.Row

    try:
        # 1. Get all account IDs
        cur = await src.execute("SELECT id FROM accounts ORDER BY id")
        account_ids = [r[0] for r in await cur.fetchall()]
        logger.info(f"Found {len(account_ids)} accounts: {account_ids}")

        if dry_run:
            logger.info("DRY RUN — no files will be created")
            for table in PLATFORM_TABLES:
                if await table_exists(src, table):
                    cur = await src.execute(f"SELECT COUNT(*) FROM {table}")
                    count = (await cur.fetchone())[0]
                    summary["platform"][table] = count
                    logger.info(f"  platform.{table}: {count} rows")

            for acct_id in account_ids:
                summary["tenants"][acct_id] = {}
                for table in TENANT_TABLES:
                    if await table_exists(src, table):
                        cur = await src.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE account_id = ?",
                            (acct_id,),
                        )
                        count = (await cur.fetchone())[0]
                        summary["tenants"][acct_id][table] = count
                        if count > 0:
                            logger.info(f"  tenant_{acct_id}.{table}: {count} rows")
                for table, spec in TENANT_TABLES_VIA_JOIN.items():
                    if await table_exists(src, table):
                        cur = await src.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE {spec['count_where']}",
                            (acct_id,),
                        )
                        count = (await cur.fetchone())[0]
                        summary["tenants"][acct_id][table] = count
                        if count > 0:
                            logger.info(f"  tenant_{acct_id}.{table}: {count} rows")
            return summary

        # 2. Create platform.db
        os.makedirs(out_dir, exist_ok=True)
        platform = await aiosqlite.connect(platform_path)
        platform.row_factory = aiosqlite.Row
        await platform.execute("PRAGMA journal_mode=WAL")
        await platform.execute("PRAGMA foreign_keys=ON")
        await platform_schema.create_tables(platform)

        for table in PLATFORM_TABLES:
            if await table_exists(src, table):
                count = await copy_table(src, platform, table)
                summary["platform"][table] = count
                logger.info(f"  platform.{table}: {count} rows")
            else:
                logger.info(f"  platform.{table}: table not found (skipped)")

        await platform.close()

        # 3. Create per-tenant DBs
        os.makedirs(tenants_dir, exist_ok=True)

        for acct_id in account_ids:
            tenant_path = os.path.join(tenants_dir, f"tenant_{acct_id}.db")
            tenant = await aiosqlite.connect(tenant_path)
            tenant.row_factory = aiosqlite.Row
            await tenant.execute("PRAGMA journal_mode=WAL")
            await tenant.execute("PRAGMA foreign_keys=ON")
            await tenant_schema.create_tables(tenant)

            summary["tenants"][acct_id] = {}
            for table in TENANT_TABLES:
                if await table_exists(src, table):
                    count = await copy_table(
                        src, tenant, table,
                        where="account_id = ?", params=(acct_id,),
                    )
                    summary["tenants"][acct_id][table] = count
                    if count > 0:
                        logger.info(f"  tenant_{acct_id}.{table}: {count} rows")
                else:
                    logger.info(f"  tenant_{acct_id}.{table}: table not found (skipped)")

            # Tables without direct account_id (via JOIN)
            for table, spec in TENANT_TABLES_VIA_JOIN.items():
                if await table_exists(src, table):
                    count = await copy_table(
                        src, tenant, table,
                        where=spec["where"], params=(acct_id,),
                        join=spec["join"],
                    )
                    summary["tenants"][acct_id][table] = count
                    if count > 0:
                        logger.info(f"  tenant_{acct_id}.{table}: {count} rows")

            await tenant.close()

        logger.info("Migration complete!")

    finally:
        await src.close()

    return summary


async def verify(db_path: str, out_dir: str) -> bool:
    """Verify migration by comparing row counts."""
    platform_path = os.path.join(out_dir, "platform.db")
    tenants_dir = os.path.join(out_dir, "tenants")

    src = await aiosqlite.connect(db_path + ".backup")
    platform = await aiosqlite.connect(platform_path)

    ok = True

    try:
        # Verify platform tables
        for table in PLATFORM_TABLES:
            if not await table_exists(src, table):
                continue
            cur = await src.execute(f"SELECT COUNT(*) FROM {table}")
            src_count = (await cur.fetchone())[0]
            cur = await platform.execute(f"SELECT COUNT(*) FROM {table}")
            dst_count = (await cur.fetchone())[0]
            if src_count != dst_count:
                logger.error(f"MISMATCH platform.{table}: {src_count} vs {dst_count}")
                ok = False
            else:
                logger.info(f"  platform.{table}: {src_count} rows ✓")

        # Verify tenant tables
        cur = await src.execute("SELECT id FROM accounts ORDER BY id")
        account_ids = [r[0] for r in await cur.fetchall()]

        for acct_id in account_ids:
            tenant_path = os.path.join(tenants_dir, f"tenant_{acct_id}.db")
            if not os.path.exists(tenant_path):
                logger.error(f"Missing tenant DB: {tenant_path}")
                ok = False
                continue

            tenant = await aiosqlite.connect(tenant_path)

            all_tenant_tables = list(TENANT_TABLES) + list(TENANT_TABLES_VIA_JOIN.keys())
            for table in all_tenant_tables:
                if not await table_exists(src, table):
                    continue
                # Source count
                if table in TENANT_TABLES_VIA_JOIN:
                    spec = TENANT_TABLES_VIA_JOIN[table]
                    cur = await src.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {spec['count_where']}",
                        (acct_id,),
                    )
                else:
                    cur = await src.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE account_id = ?",
                        (acct_id,),
                    )
                src_count = (await cur.fetchone())[0]
                cur = await tenant.execute(f"SELECT COUNT(*) FROM {table}")
                dst_count = (await cur.fetchone())[0]
                if src_count != dst_count:
                    logger.error(
                        f"MISMATCH tenant_{acct_id}.{table}: {src_count} vs {dst_count}"
                    )
                    ok = False
                elif src_count > 0:
                    logger.info(f"  tenant_{acct_id}.{table}: {src_count} rows ✓")
            await tenant.close()

    finally:
        await src.close()
        await platform.close()

    return ok


def main():
    parser = argparse.ArgumentParser(description="Migrate bot.db to multi-tenant layout")
    parser.add_argument("--db", default="data/bot.db", help="Path to source bot.db")
    parser.add_argument("--out", default="data/", help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be migrated")
    parser.add_argument("--verify", action="store_true", help="Verify migration after completion")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.dry_run:
        summary = asyncio.run(migrate(args.db, args.out, dry_run=True))
        print(f"\nDry run summary: {len(summary.get('tenants', {}))} tenants")
    elif args.verify:
        ok = asyncio.run(verify(args.db, args.out))
        sys.exit(0 if ok else 1)
    else:
        summary = asyncio.run(migrate(args.db, args.out))
        print(f"\nMigration complete: {len(summary.get('tenants', {}))} tenants created")
        print("Run with --verify to check row counts")


if __name__ == "__main__":
    main()
