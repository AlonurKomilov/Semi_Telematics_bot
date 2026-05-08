"""FastAPI application factory."""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from interfaces.api.routes import fleet, maps, alerts, health, pois, geofences
from interfaces.api.routes import vehicles as vehicles_routes
from interfaces.api.routes import parking as parking_routes
from interfaces.api.routes import routes as dispatch_routes
from interfaces.api.routes import safety as safety_routes
from interfaces.api.routes import reports as reports_routes
from interfaces.api.routes import costs as costs_routes
from interfaces.api.routes import user as user_routes
from interfaces.api.routes import admin as admin_routes
from interfaces.api.routes import maintenance as maintenance_routes
from interfaces.api.routes import ai as ai_routes
from interfaces.api.routes import knowledge as knowledge_routes
from interfaces.api.routes import permissions as permissions_routes
from interfaces.api.routes import billing as billing_routes
from interfaces.api.routes import files as files_routes
from interfaces.api.routes import payroll as payroll_routes
from interfaces.api.routes import coaching as coaching_routes
from interfaces.api.auth import router as auth_router
from interfaces.api.rate_limit import limiter

logger = logging.getLogger(__name__)

# ── Allowed CORS origins ─────────────────────────────────────────
_ALLOWED_ORIGINS = [
    "https://4truck.us",
    "https://www.4truck.us",
    "https://web.telegram.org",
    "https://weba.telegram.org",
    "https://webk.telegram.org",
]
# Allow override via env var (comma-separated) for dev/staging
_extra = os.getenv("CORS_ALLOWED_ORIGINS", "")
if _extra:
    _ALLOWED_ORIGINS.extend(o.strip() for o in _extra.split(",") if o.strip())

# Max request body size (bytes) — 2 MB at app level; nginx also enforces 5 MB
_MAX_BODY_SIZE = int(os.getenv("MAX_BODY_SIZE", str(2 * 1024 * 1024)))


class LimitBodyMiddleware(BaseHTTPMiddleware):
    """Reject requests with Content-Length exceeding the configured limit."""

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > _MAX_BODY_SIZE:
            return Response("Request body too large", status_code=413)
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique request ID to every request for tracing."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class ApiNoStoreMiddleware(BaseHTTPMiddleware):
    """Phase 4: stamp ``Cache-Control: no-store`` on every /api/* response.

    Defensive guard for the Cloudflare rollout. Cloudflare's default
    behaviour is to NOT cache anything with an Authorization header,
    but a misconfigured Page Rule could enable it. ``no-store`` makes
    the API response uncacheable at every layer (browser cache, CDN,
    proxy) regardless of upstream config — eliminates the risk that
    one tenant's data is served to another via a stale cache.

    Endpoints that explicitly opt into caching (none today) can override
    by setting their own ``Cache-Control`` before this middleware runs;
    BaseHTTPMiddleware applies in reverse order of registration so this
    one fires LAST in the response path. We only stamp when the header
    is missing so per-route overrides win.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Per-worker startup/shutdown for the API process.

    Runs once when each gunicorn worker boots. The legacy ``run.py``
    entry point calls ``infra.startup.initialize()`` itself before
    starting uvicorn, so when this module is imported from ``run.py``
    the ``infra.startup.tenant_registry`` global is already populated
    and we skip re-initialising. Under gunicorn (where this app factory
    is the entry point) we initialise per-worker so each worker has its
    own Redis pool, DB connections, and tenant registry.

    Per-worker initialisation is intentional: workers are separate
    processes and CANNOT share asyncio event loops, aiohttp sessions,
    or asyncpg pools across forks. The DB-encrypt migration that
    ``initialize()`` triggers is idempotent so running it once per
    worker is safe (just wasteful — the second worker's call is a
    no-op).

    Phase 6: also kicks off the ARQ queue-depth poller so Prometheus
    has a Gauge to scrape. Cancelled cleanly on shutdown.
    """
    import asyncio
    import infra.startup as _startup
    _owns_lifecycle = _startup.tenant_registry is None
    if _owns_lifecycle:
        await _startup.initialize()
        logger.info("API lifespan: initialised platform (gunicorn worker mode)")
    else:
        logger.info("API lifespan: reusing platform from parent (legacy run.py mode)")

    # Phase 6: queue-depth poller (no-op when prometheus deps absent).
    from infra import observability as _obs
    queue_poller = asyncio.create_task(
        _obs.poll_arq_queue_depth(), name="arq-queue-depth-poller",
    )
    try:
        yield
    finally:
        queue_poller.cancel()
        try:
            await queue_poller
        except (asyncio.CancelledError, Exception):
            pass
        # Only tear down what we set up — if run.py owns the lifecycle,
        # let run.py shut everything down at SIGTERM.
        if _owns_lifecycle:
            await _startup.shutdown()
            logger.info("API lifespan: shut down platform")


def create_api() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="4truck API",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=_lifespan,
    )

    # Rate limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Global exception handler — structured JSON for unhandled errors
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error(
            "Unhandled error [request_id=%s] %s %s: %s",
            request_id, request.method, request.url.path, exc,
            exc_info=True,
        )
        try:
            from infra.error_reporter import report_error
            _path = f"{request.method} {request.url.path}"
            asyncio.create_task(report_error(exc, source="api", job_name=_path))
        except Exception:
            pass
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "request_id": request_id,
            },
        )

    # Request ID for distributed tracing
    app.add_middleware(RequestIDMiddleware)

    # Phase 4: API responses must never hit a CDN cache. We stamp
    # no-store on every /api/* response unless the route handler set
    # an explicit Cache-Control header itself. Registered close to the
    # innermost so the route handler's own headers take precedence.
    app.add_middleware(ApiNoStoreMiddleware)

    # Request body size limit
    app.add_middleware(LimitBodyMiddleware)

    # Compress JSON responses ≥500 B with gzip — typical fleet payloads
    # (vehicles, events, scorecards) shrink ~70 %. Below 500 B the gzip
    # framing overhead exceeds the savings. Browsers send Accept-Encoding
    # automatically; this middleware only kicks in when they advertise gzip.
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # CORS — explicit allowed origins (wildcard + credentials is spec-violating)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # API routes — versioned under /api/v1, with /api as backward-compat alias
    for prefix in ("/api/v1", "/api"):
        app.include_router(health.router, prefix=prefix)
        app.include_router(auth_router, prefix=prefix)
        app.include_router(user_routes.router, prefix=prefix)
        app.include_router(fleet.router, prefix=prefix)
        app.include_router(fleet.legacy_router, prefix=prefix)
        app.include_router(vehicles_routes.router, prefix=prefix)
        app.include_router(maps.router, prefix=prefix)
        app.include_router(pois.router, prefix=prefix)
        app.include_router(geofences.router, prefix=prefix)
        app.include_router(geofences.legacy_router, prefix=prefix)
        app.include_router(alerts.router, prefix=prefix)
        app.include_router(parking_routes.router, prefix=prefix)
        app.include_router(dispatch_routes.router, prefix=prefix)
        app.include_router(dispatch_routes.legacy_router, prefix=prefix)
        app.include_router(safety_routes.router, prefix=prefix)
        app.include_router(safety_routes.legacy_router, prefix=prefix)
        app.include_router(reports_routes.router, prefix=prefix)
        app.include_router(costs_routes.router, prefix=prefix)
        app.include_router(admin_routes.router, prefix=prefix)
        app.include_router(permissions_routes.router, prefix=prefix)
        app.include_router(maintenance_routes.router, prefix=prefix)
        app.include_router(ai_routes.router, prefix=prefix)
        app.include_router(knowledge_routes.router, prefix=prefix)
        app.include_router(billing_routes.router, prefix=prefix)
        app.include_router(files_routes.router, prefix=prefix)
        app.include_router(payroll_routes.router, prefix=prefix)
        app.include_router(coaching_routes.router, prefix=prefix)

    # ── Phase 6: observability ─────────────────────────────
    # Wire /metrics + OTel auto-instrumentation BEFORE the static
    # mounts so /metrics is reachable at the API root, not under /miniapp/.
    from infra import observability as _obs
    _obs.init_observability(app)

    # Serve miniapp static files (Telegram Mini App) — built via `npm run build`
    miniapp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "miniapp", "dist")
    if os.path.isdir(miniapp_dir):
        miniapp_index = os.path.join(miniapp_dir, "index.html")

        # SPA catch-all: serve index.html for any /miniapp/* path (Vite base is '/miniapp/')
        @app.get("/miniapp/{full_path:path}")
        async def miniapp_spa(full_path: str):
            file_path = os.path.join(miniapp_dir, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            # no-store so the browser always fetches fresh HTML after a deploy
            # (hashed JS/CSS assets are safe to cache; the HTML must not be)
            return FileResponse(
                miniapp_index,
                headers={"Cache-Control": "no-store, must-revalidate"},
            )

        app.mount("/miniapp", StaticFiles(directory=miniapp_dir, html=True), name="miniapp")

    # Serve dashboard static files (desktop)
    dashboard_dir = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "dashboard", "dist"
    )
    if os.path.isdir(dashboard_dir):
        dashboard_index = os.path.join(dashboard_dir, "index.html")

        # SPA catch-all: serve index.html for any /dashboard/* path that
        # doesn't match a real static file (e.g. /dashboard/fleet/weather)
        @app.get("/dashboard/{full_path:path}")
        async def dashboard_spa(full_path: str):
            # If the path maps to a real file (JS/CSS/images), serve it
            file_path = os.path.join(dashboard_dir, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            # no-store so the browser always fetches fresh HTML after a deploy
            # (hashed JS/CSS assets are safe to cache; the HTML must not be)
            return FileResponse(
                dashboard_index,
                headers={"Cache-Control": "no-store, must-revalidate"},
            )

        app.mount(
            "/dashboard",
            StaticFiles(directory=dashboard_dir, html=True),
            name="dashboard",
        )

    return app


# ── Gunicorn / uvicorn entry point ────────────────────────────
# Gunicorn imports this module and looks up ``app`` as the ASGI
# application:
#
#   gunicorn -c gunicorn.conf.py interfaces.api.app:app
#
# The module-level call is safe — ``create_api()`` only constructs the
# FastAPI object and registers routes; no connections are opened until
# the lifespan startup handler runs. ``run.py`` still uses
# ``create_api()`` directly so single-process dev keeps working.
app = create_api()
