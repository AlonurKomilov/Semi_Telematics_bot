"""Schema migrations for per-tenant database tables.

These run after tenant_schema.create_tables() and add columns/indexes
introduced after the initial multi-tenant schema was created.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_all(conn) -> None:
    """Execute every tenant migration in order."""
    # Future migrations go here, e.g.:
    # await migrate_add_maintenance_priority(conn)
    pass
