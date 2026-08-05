"""Health check endpoints.

``/health``      — minimal liveness probe used by Docker HEALTHCHECK and CI.
``/health/deps`` — per-dependency status snapshot for ops.  When the next
                   incident hits, one curl shows which upstream is dark
                   (DB / Redis / Samsara) instead of grepping logs.

Both endpoints intentionally return 200 in all cases.  Their *body*
describes the state; turning the endpoint itself into a 5xx would defeat
the diagnostic purpose (load balancers would mask the response).
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Basic health check — used by Docker HEALTHCHECK and CI/CD."""
    import infra.cache as rcache

    # Check DB
    db_ok = False
    try:
        from infra.platform import get_db
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

    # Check the BOT process (separate service): its capacity sampler
    # writes a minute row every 60s — that pulse is readable from here.
    # An external watcher keyword-matching '"status":"ok"' therefore
    # catches a dead bot even while this API answers fine — the one
    # failure mode every in-process alerter is structurally blind to
    # (proven by a 20-hour unalerted outage, 2026-08-04).
    bot_status = "unknown"
    if db_ok:
        try:
            from capabilities.platform.capacity.sampler import (
                bot_heartbeat_age_min,
            )
            age = await bot_heartbeat_age_min(db)
            if age is not None:
                bot_status = "ok" if age <= 3 else "silent"
        except Exception:
            pass  # stays "unknown" — never break the liveness probe

    status = "ok" if db_ok and bot_status != "silent" else "degraded"
    return {
        "status": status,
        "db": "ok" if db_ok else "error",
        "redis": redis_status,
        "bot": bot_status,
    }


@router.get("/health/deps")
async def deps_health():
    """Per-dependency status snapshot.

    Probes the things that can drag the API down when they go dark:
    Postgres, Redis, and Samsara (via the circuit-breaker snapshot —
    no live HTTP call so this endpoint stays fast and free of side
    effects).  Gemini is reported as "configured" based on env presence
    rather than pinged, because active probes would burn paid tokens
    on every health check.

    Always returns 200 — the JSON body is the diagnostic.  Use the
    top-level ``status`` field for a quick "anything broken?" read:
    ``ok`` when every dep is healthy, ``degraded`` otherwise.
    """
    import infra.cache as rcache

    deps: dict[str, dict] = {}

    # ── Postgres ─────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        from infra.platform import get_db
        db = get_db()
        async with db.acquire() as conn:
            await conn.execute("SELECT 1")
        deps["db"] = {
            "status": "ok",
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        }
    except Exception as e:
        deps["db"] = {
            "status": "error",
            "error": f"{type(e).__name__}: {e}"[:200],
        }

    # ── Redis ────────────────────────────────────────────────────
    if rcache._pool is None:
        deps["redis"] = {"status": "disabled"}
    else:
        t0 = time.monotonic()
        try:
            await rcache._pool.ping()
            deps["redis"] = {
                "status": "ok",
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
        except Exception as e:
            deps["redis"] = {
                "status": "error",
                "error": f"{type(e).__name__}: {e}"[:200],
            }

    # ── Samsara (circuit-breaker snapshot, no live HTTP) ─────────
    try:
        from adapters.samsara.circuit_breaker import samsara_breaker
        snap = samsara_breaker.state_snapshot()
        # Map breaker state → simple status word.
        # closed = healthy; half_open = recovering; open = upstream dark.
        breaker_state = snap.get("state", "unknown")
        status_word = {
            "closed": "ok",
            "half_open": "recovering",
            "open": "error",
        }.get(breaker_state, "unknown")
        deps["samsara"] = {"status": status_word, **snap}
    except Exception as e:
        deps["samsara"] = {
            "status": "unknown",
            "error": f"{type(e).__name__}: {e}"[:200],
        }

    # ── Gemini / Vertex AI (configured-only, no live call) ───────
    gemini_configured = bool(
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        or os.getenv("VERTEX_AI_PROJECT")
    )
    deps["gemini"] = {
        "status": "configured" if gemini_configured else "disabled",
        "model": os.getenv("VERTEX_AI_MODEL", "gemini-2.5-flash"),
    }

    # ── Top-level rollup ────────────────────────────────────────
    # "ok" only when every probed-live dep is ok.  Disabled/configured
    # don't count against us (they're reachable-by-design states).
    healthy_states = {"ok", "disabled", "configured"}
    overall = "ok" if all(
        d.get("status") in healthy_states for d in deps.values()
    ) else "degraded"

    return {"status": overall, "deps": deps}
