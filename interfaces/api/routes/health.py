"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check — used by Docker HEALTHCHECK and CI/CD."""
    import adapters.cache.redis as rcache

    # Check DB
    db_ok = False
    try:
        from core.platform import get_db
        db = get_db()
        async with db.acquire() as conn:
            await conn.execute("SELECT 1")
            db_ok = True
    except Exception:
        pass  # db_ok stays False — reported in health response

    # Check Redis
    redis_status = "disabled"
    if rcache._pool is not None:
        try:
            await rcache._pool.ping()
            redis_status = "ok"
        except Exception:
            redis_status = "error"

    status = "ok" if db_ok else "degraded"
    return {
        "status": status,
        "db": "ok" if db_ok else "error",
        "redis": redis_status,
    }
