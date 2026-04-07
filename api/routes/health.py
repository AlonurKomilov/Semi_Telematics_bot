"""Health check endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check — used by Docker HEALTHCHECK and CI/CD."""
    from bot.state import db
    import bot.redis_client as rcache

    # Check DB
    db_ok = False
    try:
        await db._execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    # Check Redis
    redis_status = "disabled"
    if rcache._redis is not None:
        try:
            await rcache._redis.ping()
            redis_status = "ok"
        except Exception:
            redis_status = "error"

    status = "ok" if db_ok else "degraded"
    return {
        "status": status,
        "db": "ok" if db_ok else "error",
        "redis": redis_status,
    }
