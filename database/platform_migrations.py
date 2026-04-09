"""Schema migrations for platform database tables.

These run after platform_schema.create_tables() and add columns/indexes
introduced after the initial multi-tenant schema was created.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_all(conn) -> None:
    """Execute every platform migration in order."""
    # Future migrations go here, e.g.:
    # await migrate_add_account_billing(conn)
    pass
