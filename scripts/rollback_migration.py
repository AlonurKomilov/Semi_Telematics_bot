#!/usr/bin/env python3
"""Rollback multi-tenant migration — restore original bot.db from backup.

Usage:
    python3 scripts/rollback_migration.py [--db data/bot.db] [--out data/]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys

logger = logging.getLogger(__name__)


def rollback(db_path: str, out_dir: str) -> None:
    """Restore bot.db from backup and remove multi-tenant files."""
    backup_path = db_path + ".backup"
    platform_path = os.path.join(out_dir, "platform.db")
    tenants_dir = os.path.join(out_dir, "tenants")

    if not os.path.exists(backup_path):
        logger.error(f"No backup found at {backup_path}")
        sys.exit(1)

    # Remove multi-tenant files
    if os.path.exists(platform_path):
        os.remove(platform_path)
        logger.info(f"Removed {platform_path}")
        # Also remove WAL/SHM files
        for ext in ("-wal", "-shm"):
            p = platform_path + ext
            if os.path.exists(p):
                os.remove(p)

    if os.path.exists(tenants_dir):
        shutil.rmtree(tenants_dir)
        logger.info(f"Removed {tenants_dir}/")

    # Restore original
    shutil.copy2(backup_path, db_path)
    logger.info(f"Restored {backup_path} → {db_path}")
    logger.info("Rollback complete. You can now restart the application.")


def main():
    parser = argparse.ArgumentParser(description="Rollback multi-tenant migration")
    parser.add_argument("--db", default="data/bot.db", help="Path to bot.db")
    parser.add_argument("--out", default="data/", help="Output directory used during migration")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rollback(args.db, args.out)


if __name__ == "__main__":
    main()
