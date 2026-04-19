#!/usr/bin/env python3
"""Entry point for Semi Telematics Bot + FastAPI API server.

Runs the Telegram bot and the FastAPI (uvicorn) server concurrently
in a single asyncio event loop.

Startup order:
  1. core.startup.initialize()  — encryption, DB, AI, Redis, key migration
  2. build_app()                — Telegram Application with all handlers
  3. APScheduler                — scheduled alert/report jobs
  4. run_api()                  — FastAPI uvicorn server (background task)
  5. run_bot()                  — Telegram polling or webhook
  6. await stop_event           — block until SIGINT/SIGTERM
  7. core.startup.shutdown()    — Redis, DB, caches
"""

import asyncio
import os
import signal

from dotenv import load_dotenv
load_dotenv()

from telegram import Update                       # noqa: E402
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402

from bot.app import build_app                      # noqa: E402
from bot.config import (                           # noqa: E402
    WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_SECRET, USE_WEBHOOK,
    logger,
)
from bot.scheduler import register_all as _register_jobs  # noqa: E402
from core.bot_registry import init_registry         # noqa: E402
import core.startup                                # noqa: E402


async def run_bot(tg_app):
    """Start the Telegram bot (non-blocking)."""
    await tg_app.initialize()

    # post_init is only called automatically by run_polling()/run_webhook(),
    # not by the manual initialize→start flow we use here.
    if tg_app.post_init:
        await tg_app.post_init(tg_app)

    if USE_WEBHOOK:
        if not WEBHOOK_SECRET:
            logger.warning(
                "WEBHOOK_SECRET is not set! Webhook requests will not be validated."
            )
        logger.info("Starting webhook mode on port %s", WEBHOOK_PORT)
        await tg_app.updater.start_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path="/webhook",
            webhook_url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET or None,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting polling mode")
        await tg_app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    await tg_app.start()


async def run_api():
    """Start the FastAPI server via uvicorn."""
    try:
        import uvicorn
        from api.app import create_api
    except ImportError:
        logger.info("FastAPI/uvicorn not installed — API server disabled")
        return

    api = create_api()
    api_port = int(os.environ.get("API_PORT", "8000"))
    config = uvicorn.Config(api, host="0.0.0.0", port=api_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    logger.info("Starting Semi Telematics Bot — multi-tenant mode")

    # ── 1. Platform infrastructure ──────────────────────────────
    await core.startup.initialize()

    # ── 2. Build Telegram Application ───────────────────────────
    tg_app = build_app()

    # ── 2b. Per-account bot registry ────────────────────────────
    registry = init_registry(system_app=tg_app)
    try:
        from core.platform import get_platform_db
        started = await registry.start_all(get_platform_db())
        if started:
            logger.info("Started %d per-account bot(s) from database", started)
    except Exception:
        logger.exception("Failed to start per-account bots (continuing with system bot)")

    # ── 3. Scheduled alerts ─────────────────────────────────────
    scheduler = AsyncIOScheduler()
    _register_jobs(scheduler, tg_app)
    scheduler.start()

    # ── 4. Graceful shutdown wiring ─────────────────────────────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # ── 5. Start API server (background) ───────────────────────
    api_task = asyncio.create_task(run_api())

    # ── 6. Start bot (post_init may take time) ──────────────────
    await run_bot(tg_app)

    # ── 7. Wait for shutdown signal ─────────────────────────────
    await stop_event.wait()
    logger.info("Shutdown signal received")

    # ── 8. Cleanup ──────────────────────────────────────────────
    api_task.cancel()
    await registry.stop_all()
    if tg_app.updater.running:
        await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    scheduler.shutdown(wait=False)

    # Platform shutdown (Redis, DB, caches)
    await core.startup.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
