"""Spawn-only API worker for isolated transport probes; never runs migrations."""
import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse


def serve(database_url, redis_url, listener, ready):
    # This helper only accepts pytest's copied database on a loopback container.
    parsed = urlparse(database_url)
    assert parsed.hostname in ('127.0.0.1', 'localhost') and '_t' in parsed.path
    import os
    os.environ['BILLING_ENFORCEMENT_ENABLED'] = '0'
    os.environ['QUARANTINE_ENFORCEMENT_ENABLED'] = '0'
    os.environ['SUITE_REPORT_URL'] = ''
    os.environ['REDIS_URL'] = redis_url
    os.environ['REDIS_SENTINELS'] = ''
    import uvicorn
    from redis.asyncio import Redis
    from adapters.storage import Database
    from adapters.storage.pg_adapter import open_pg_pool, _PgConnection
    from capabilities.chat.realtime import ChatRuntime, RedisSignals
    from capabilities.permissions.plans import refresh_plans
    from interfaces.api.app import create_api
    import interfaces.api.deps as deps
    import infra.platform as platform
    import infra.cache as cache

    @asynccontextmanager
    async def lifespan(app):
        db = Database(pool_size=4)
        db._pg_pool = await open_pg_pool(database_url, pool_size=4)
        db._db = _PgConnection(db._pg_pool)
        platform._db = db
        deps._fire_heartbeats = lambda _: None
        await refresh_plans(db, force=True)
        redis = Redis.from_url(redis_url, socket_connect_timeout=.5, socket_timeout=.5)
        cache._pool, cache._available = redis, True
        runtime = await ChatRuntime(db, RedisSignals(cache.get_pubsub_client)).start()
        app.state.chat_runtime = runtime
        ready.send(True)
        try:
            yield
        finally:
            await runtime.stop()
            cache._pool, cache._available = None, False
            await redis.aclose()
            await db.close()

    app = create_api()
    app.router.lifespan_context = lifespan
    config = uvicorn.Config(app, log_level='error', access_log=False, ws_max_size=4096, ws_max_queue=4)
    asyncio.run(uvicorn.Server(config).serve(sockets=[listener]))
