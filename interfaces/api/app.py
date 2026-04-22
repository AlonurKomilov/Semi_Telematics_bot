"""FastAPI application factory."""

import logging
import os
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

from interfaces.api.routes import fleet, maps, alerts, health
from interfaces.api.routes import parking as parking_routes
from interfaces.api.routes import routes as dispatch_routes
from interfaces.api.routes import safety as safety_routes
from interfaces.api.routes import reports as reports_routes
from interfaces.api.routes import costs as costs_routes
from interfaces.api.routes import user as user_routes
from interfaces.api.routes import dashboard as dashboard_routes
from interfaces.api.routes import admin as admin_routes
from interfaces.api.routes import maintenance as maintenance_routes
from interfaces.api.routes import ai as ai_routes
from interfaces.api.routes import knowledge as knowledge_routes
from interfaces.api.routes import permissions as permissions_routes
from interfaces.api.routes import billing as billing_routes
from interfaces.api.routes import work_hours as work_hours_routes
from interfaces.api.auth import router as auth_router
from interfaces.api.rate_limit import limiter

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


def create_api() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Semi Telematics API",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
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
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "request_id": request_id,
            },
        )

    # Request ID for distributed tracing
    app.add_middleware(RequestIDMiddleware)

    # Request body size limit
    app.add_middleware(LimitBodyMiddleware)

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
        app.include_router(dashboard_routes.router, prefix=prefix)
        app.include_router(fleet.router, prefix=prefix)
        app.include_router(maps.router, prefix=prefix)
        app.include_router(alerts.router, prefix=prefix)
        app.include_router(parking_routes.router, prefix=prefix)
        app.include_router(dispatch_routes.router, prefix=prefix)
        app.include_router(safety_routes.router, prefix=prefix)
        app.include_router(reports_routes.router, prefix=prefix)
        app.include_router(costs_routes.router, prefix=prefix)
        app.include_router(admin_routes.router, prefix=prefix)
        app.include_router(permissions_routes.router, prefix=prefix)
        app.include_router(maintenance_routes.router, prefix=prefix)
        app.include_router(ai_routes.router, prefix=prefix)
        app.include_router(knowledge_routes.router, prefix=prefix)
        app.include_router(billing_routes.router, prefix=prefix)
        app.include_router(work_hours_routes.router, prefix=prefix)

    # Serve webapp static files (Mini App)
    webapp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
    if os.path.isdir(webapp_dir):
        app.mount("/app", StaticFiles(directory=webapp_dir, html=True), name="webapp")

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
            # Otherwise, serve index.html for client-side routing
            return FileResponse(dashboard_index)

        app.mount(
            "/dashboard",
            StaticFiles(directory=dashboard_dir, html=True),
            name="dashboard",
        )

    return app
