"""Shared scaffolding for the data-lifecycle hubs (rollups + retention).

Both hubs do the same two generic things: fan a per-account coroutine across
every active account, and discover their feature contributors by importing a
list of modules.  Those helpers live here — not in the telemetry ingestor —
so neither hub has to reach into a feature module for them (they aren't
telemetry-specific), and the discover boilerplate isn't copy-pasted per hub.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from typing import Awaitable, Callable

from infra.services import get_platform_db, get_tenant_db

logger = logging.getLogger(__name__)

# Bound per-account concurrency so a fan-out doesn't burst a provider API or
# saturate the Postgres pool.  Shared by ingest + both lifecycle hubs.
_MAX_CONCURRENT_ACCOUNTS = int(os.getenv("INGEST_MAX_CONCURRENT_ACCOUNTS", "5"))


async def for_each_active_account(coro_factory: Callable[[int], Awaitable]) -> None:
    """Invoke ``coro_factory(account_id)`` for every active account in
    parallel (bounded concurrency), catching per-account errors so one bad
    tenant doesn't block the rest of the fleet.

    Each per-account job stamps the RLS GUC (``app.account_id``) for its
    tenant connection — without it, RLS-enforced mode returns zero rows and
    silently breaks every per-account job.
    """
    job_name = getattr(coro_factory, "__name__", "anon")
    t0 = _time.perf_counter()
    try:
        accounts = await get_platform_db().list_accounts(active_only=True)
    except Exception:
        logger.exception("lifecycle: list_accounts failed")
        return
    if not accounts:
        return

    sem = asyncio.Semaphore(_MAX_CONCURRENT_ACCOUNTS)
    per_acct_ms: dict[int, float] = {}

    async def _run(acc):
        async with sem:
            t_acct = _time.perf_counter()
            try:
                tenant_db = await get_tenant_db(acc.id)
                async with tenant_db.with_account(acc.id):
                    await coro_factory(acc.id)
            except Exception:
                logger.exception("lifecycle: per-account job failed acct=%d", acc.id)
            finally:
                per_acct_ms[acc.id] = round(
                    (_time.perf_counter() - t_acct) * 1000, 1,
                )

    await asyncio.gather(*(_run(a) for a in accounts))
    total_ms = round((_time.perf_counter() - t0) * 1000, 1)
    if per_acct_ms:
        slowest_aid = max(per_acct_ms, key=per_acct_ms.get)
        logger.info(
            "lifecycle job=%s accounts=%d total_ms=%s slowest_acct=%d slowest_ms=%s",
            job_name, len(accounts), total_ms,
            slowest_aid, per_acct_ms[slowest_aid],
        )


def make_discover(contributors: tuple[str, ...]) -> Callable[[], None]:
    """Build a lazy, idempotent ``discover()`` that imports each contributor
    module so its ``register_*`` calls fire.  Missing modules are logged, not
    fatal.  Shared by both hubs so the "collect feature contributions"
    boilerplate lives in one place.
    """
    state = {"done": False}

    def discover() -> None:
        if state["done"]:
            return
        import importlib

        for mod in contributors:
            try:
                importlib.import_module(mod)
            except Exception:
                logger.exception("lifecycle: failed to load contributor module %s", mod)
        state["done"] = True

    return discover
