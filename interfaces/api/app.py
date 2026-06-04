"""FastAPI application factory."""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
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
from interfaces.api.routes import system as system_routes
from interfaces.api.routes import maintenance as maintenance_routes
from interfaces.api.routes import work_orders as work_orders_routes
from interfaces.api.routes import inspections as inspections_routes
from interfaces.api.routes import storage as storage_routes
from interfaces.api.routes import ai as ai_routes
from interfaces.api.routes import knowledge as knowledge_routes
from interfaces.api.routes import permissions as permissions_routes
from interfaces.api.routes import billing as billing_routes
from interfaces.api.routes import payroll as payroll_routes
from interfaces.api.routes import coaching as coaching_routes
from interfaces.api.routes import drivers as drivers_routes
from interfaces.api.auth import router as auth_router
from interfaces.api.rate_limit import limiter

logger = logging.getLogger(__name__)

# ── Allowed CORS origins ─────────────────────────────────────────
# The dedicated subdomains (dash/api/app/bot) replace the apex /dashboard
# /api /miniapp /webhook paths.  Apex is kept in the allowlist so legacy
# bookmarks still authenticate during the 301-redirect window.
_ALLOWED_ORIGINS = [
    "https://4truck.us",
    "https://www.4truck.us",
    "https://dash.4truck.us",
    "https://api.4truck.us",
    "https://app.4truck.us",
    "https://bot.4truck.us",
    "https://web.telegram.org",
    "https://weba.telegram.org",
    "https://webk.telegram.org",
]
# Allow override via env var (comma-separated) for dev/staging
_extra = os.getenv("CORS_ALLOWED_ORIGINS", "")
if _extra:
    _ALLOWED_ORIGINS.extend(o.strip() for o in _extra.split(",") if o.strip())

# Max request body size — two limits so JSON endpoints keep a tight DoS
# cap while file-upload endpoints get the headroom they need.  Routes
# enforce their own per-endpoint caps (work-order attachments 10 MB,
# driver documents 20 MB, POI CSV 5 MB); the middleware is the outer
# envelope so a malformed payload can't tie up gunicorn parsing a
# multi-MB body before the route's own size check rejects it.
_MAX_BODY_SIZE = int(os.getenv("MAX_BODY_SIZE", str(2 * 1024 * 1024)))
_MAX_UPLOAD_BODY_SIZE = int(os.getenv("MAX_UPLOAD_BODY_SIZE", str(25 * 1024 * 1024)))

# Path *suffixes* (post-prefix) that accept large multipart uploads.
# Match by ``str.endswith`` so /api and /api/v1 prefixes both work.
_UPLOAD_PATH_SUFFIXES = (
    "/attachments",   # work-order invoice/photo/warranty uploads (10 MB cap in-route)
    "/documents",     # driver license/medical/insurance uploads (20 MB cap in-route)
)


class LimitBodyMiddleware(BaseHTTPMiddleware):
    """Reject requests with Content-Length exceeding the configured limit.

    Picks the cap by path: upload endpoints get
    ``MAX_UPLOAD_BODY_SIZE`` (25 MB default), every other endpoint gets
    ``MAX_BODY_SIZE`` (2 MB default).  POST is the only method that
    carries uploaded bytes here — the predicate doesn't gate on method
    so a misrouted GET still gets the same 413.
    """

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl:
            path = request.url.path
            limit = (
                _MAX_UPLOAD_BODY_SIZE
                if any(path.endswith(s) for s in _UPLOAD_PATH_SUFFIXES)
                else _MAX_BODY_SIZE
            )
            if int(cl) > limit:
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
    """stamp ``Cache-Control: no-store`` on every /api/* response.

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


# Path suffixes (post-prefix, so /api and /api/v1 both match) that the
# enforcement middleware leaves open even when the account is blocked.
# Billing routes must stay reachable so the admin can update their card
# or open the customer portal; auth routes must stay reachable so a
# logged-out session can refresh; health/version stay open for monitors.
_ENFORCEMENT_BYPASS_SUFFIXES = (
    "/billing/summary", "/billing/usage", "/billing/invoices",
    "/billing/portal", "/billing/webhook",
    "/auth/login", "/auth/refresh", "/auth/logout",
    "/health", "/version",
)


class BillingEnforcementMiddleware(BaseHTTPMiddleware):
    """Return HTTP 402 when the JWT-bearer's account is past-due past grace.

    Gated by ``BILLING_ENFORCEMENT_ENABLED`` so this can land dark and
    flip on once the dashboard banner is shipped.  Only acts on
    ``/api/*`` paths — static assets and the public landing page are
    unaffected.  Unauthenticated requests fall through (the route's own
    auth dependency will 401 them); decode failures fall through too,
    same reason.  Bypass list keeps billing + auth + health open so a
    blocked account can still recover.
    """

    async def dispatch(self, request: Request, call_next):
        # Read the env directly each call rather than the cached
        # module-level constant in infra.config — tests need to flip
        # this with monkeypatch.setenv and that wouldn't work against a
        # constant captured at import time.  The overhead is a single
        # dict lookup per request.
        if os.getenv("BILLING_ENFORCEMENT_ENABLED", "0") != "1":
            return await call_next(request)
        grace_days = int(os.getenv("BILLING_GRACE_PERIOD_DAYS", "7"))
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if any(path.endswith(s) for s in _ENFORCEMENT_BYPASS_SUFFIXES):
            return await call_next(request)
        # Pull the JWT — header OR cookie — same fallback order the
        # ``get_current_user`` dependency uses, so a request the route
        # would consider authenticated is the same set this middleware
        # blocks.  Decode failures are treated as "no auth" and fall
        # through to the route (which will 401 if it needs auth).
        from interfaces.api.auth import AUTH_COOKIE_NAME, decode_jwt
        from jose import JWTError
        token = ""
        auth_h = request.headers.get("authorization", "")
        if auth_h.startswith("Bearer "):
            token = auth_h[7:]
        if not token:
            token = request.cookies.get(AUTH_COOKIE_NAME, "")
        if not token:
            return await call_next(request)
        try:
            payload = decode_jwt(token)
        except JWTError:
            return await call_next(request)
        account_id = payload.get("account_id")
        if not account_id:
            return await call_next(request)
        from infra.platform import get_router
        from adapters.storage.billing import BillingMixin
        try:
            platform_db = get_router().platform
            sub = await platform_db.get_subscription(int(account_id))
        except Exception:
            # DB hiccup shouldn't lock everyone out.  Log and pass; the
            # route's own queries will surface the real failure with
            # better context than a 402 here would.
            logger.warning("BillingEnforcement: subscription lookup failed for account=%s", account_id, exc_info=True)
            return await call_next(request)
        blocked, reason = BillingMixin.is_account_blocked(sub, grace_days)
        if blocked:
            return JSONResponse(
                status_code=402,
                content={
                    "detail": "Payment required",
                    "reason": reason,
                    "subscription_status": sub.get("status") if sub else "",
                    "billing_portal": "/dashboard/billing",
                },
            )
        return await call_next(request)


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

    also kicks off the ARQ queue-depth poller so Prometheus
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

    # queue-depth poller (no-op when prometheus deps absent).
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
    # slowapi's handler signature is narrower than Starlette's generic
    # ExceptionHandler (RateLimitExceeded vs the broader Exception base).
    # The runtime contract is fine — slowapi only fires this for its own
    # exception type — so silence the Pylance arg-type complaint here.
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

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

    # API responses must never hit a CDN cache. We stamp
    # no-store on every /api/* response unless the route handler set
    # an explicit Cache-Control header itself. Registered close to the
    # innermost so the route handler's own headers take precedence.
    app.add_middleware(ApiNoStoreMiddleware)

    # Request body size limit
    app.add_middleware(LimitBodyMiddleware)

    # Billing enforcement — returns 402 once a non-paying account passes
    # the grace window.  Gated by BILLING_ENFORCEMENT_ENABLED so this
    # can land dark until the dashboard's past-due banner is shipped.
    app.add_middleware(BillingEnforcementMiddleware)

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
        app.include_router(system_routes.router, prefix=prefix)
        app.include_router(permissions_routes.router, prefix=prefix)
        app.include_router(maintenance_routes.router, prefix=prefix)
        app.include_router(work_orders_routes.router, prefix=prefix)
        app.include_router(inspections_routes.router, prefix=prefix)
        app.include_router(storage_routes.router, prefix=prefix)
        app.include_router(ai_routes.router, prefix=prefix)
        app.include_router(knowledge_routes.router, prefix=prefix)
        app.include_router(billing_routes.router, prefix=prefix)
        app.include_router(payroll_routes.router, prefix=prefix)
        app.include_router(coaching_routes.router, prefix=prefix)
        app.include_router(drivers_routes.router, prefix=prefix)

    # ── observability ─────────────────────────────
    # Wire /metrics + OTel auto-instrumentation BEFORE the static
    # mounts so /metrics is reachable at the API root, not under /miniapp/.
    from infra import observability as _obs
    _obs.init_observability(app)

    # Public apex pages — landing, privacy, terms.
    # Google OAuth verification reviewers visit the app home page,
    # privacy policy, and terms URLs declared in the OAuth consent
    # screen.  All three must serve real, human-readable content (a
    # 404 at the apex is an automatic verification rejection).  Nginx
    # proxies ``/``, ``/privacy``, and ``/terms`` to FastAPI which
    # serves the static HTML below.
    _static_dir = os.path.join(os.path.dirname(__file__), "static")
    _landing_dir = os.path.join(_static_dir, "landing")
    _legal_dir = os.path.join(_static_dir, "legal")

    @app.get("/", include_in_schema=False)
    async def landing_page():
        return FileResponse(
            os.path.join(_landing_dir, "index.html"),
            media_type="text/html",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/privacy", include_in_schema=False)
    async def privacy_policy():
        return FileResponse(
            os.path.join(_legal_dir, "privacy.html"),
            media_type="text/html",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/terms", include_in_schema=False)
    async def terms_of_service():
        return FileResponse(
            os.path.join(_legal_dir, "terms.html"),
            media_type="text/html",
            headers={"Cache-Control": "public, max-age=3600"},
        )

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

        # ``index.html`` must never be cached: it contains hashed
        # ``<script src="…">`` references, and a stale cached copy will
        # ask the browser for chunk hashes that no longer exist after
        # the next deploy.  Asset files (hashed JS/CSS under
        # ``/assets/*``) are safe to cache forever — their hashes
        # change with every build, so cache invalidation is automatic.
        _DASHBOARD_NO_STORE = {"Cache-Control": "no-store, must-revalidate"}

        # Explicit root handlers.  Starlette's ``StaticFiles(html=True)``
        # serves ``index.html`` for ``/dashboard`` and ``/dashboard/``
        # without giving us a hook to set headers — without these two
        # routes, the first page-load after a deploy lands on a cached
        # HTML referencing old chunk hashes, which then 404 forever
        # (or until the user hard-refreshes).
        @app.get("/dashboard")
        @app.get("/dashboard/")
        async def dashboard_root():
            return FileResponse(dashboard_index, headers=_DASHBOARD_NO_STORE)

        # SPA catch-all: serve index.html for any /dashboard/* path that
        # doesn't match a real static file (e.g. /dashboard/fleet/weather)
        @app.get("/dashboard/{full_path:path}")
        async def dashboard_spa(full_path: str):
            # If the path maps to a real file (JS/CSS/images), serve it
            file_path = os.path.join(dashboard_dir, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            # /dashboard/assets/* are hash-named bundles emitted by Vite.
            # When the browser asks for a hash that no longer exists on
            # disk (stale index.html cached client-side after a deploy
            # rotated the hashes), we must NOT fall back to index.html —
            # the browser would receive ``text/html`` where it expects
            # ``application/javascript`` and strict-MIME checking would
            # reject the module with ``Failed to load module script``.
            # Returning 404 instead lets the dashboard's runtime catch
            # the import failure and prompt a reload to fetch the new
            # index.html (with the up-to-date hashes).
            if full_path.startswith("assets/"):
                raise HTTPException(status_code=404, detail="Asset not found")
            return FileResponse(dashboard_index, headers=_DASHBOARD_NO_STORE)

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
