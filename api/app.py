"""FastAPI application factory."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import fleet, map_data, alerts, health
from api.auth import router as auth_router


def create_api() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Semi Telematics API",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # CORS — allow Mini App and standalone web origins
    webapp_url = os.getenv("WEBAPP_URL", "")
    allowed_origins = ["*"] if not webapp_url else [
        webapp_url,
        "https://web.telegram.org",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(health.router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(fleet.router, prefix="/api")
    app.include_router(map_data.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")

    # Serve webapp static files (if directory exists)
    webapp_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")
    if os.path.isdir(webapp_dir):
        app.mount("/app", StaticFiles(directory=webapp_dir, html=True), name="webapp")

    return app
