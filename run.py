#!/usr/bin/env python3
"""Entry point for Semi Telematics Bot + FastAPI API server.

Runs the Telegram bot and the FastAPI (uvicorn) server concurrently
in a single asyncio event loop.
"""

import asyncio
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


async def run_bot(tg_app):
    """Start the Telegram bot (non-blocking)."""
    await tg_app.initialize()
    if USE_WEBHOOK:
        if not WEBHOOK_SECRET:
            logger.warning(
                "WEBHOOK_SECRET is not set! Webhook requests will not be validated."
            )
        logger.info("Starting webhook mode on port %s", WEBHOOK_PORT)
        await tg_app.updater.start_webhook(
            listen="127.0.0.1",
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
    config = uvicorn.Config(api, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    logger.info("Starting Semi Telematics Bot — multi-tenant mode")

    tg_app = build_app()

    # Scheduled alerts
    scheduler = AsyncIOScheduler()
    _register_jobs(scheduler, tg_app)
    scheduler.start()

    # Graceful shutdown
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # Start bot (non-blocking)
    await run_bot(tg_app)

    # Start API server in background task
    api_task = asyncio.create_task(run_api())

    # Wait for shutdown signal
    await stop_event.wait()
    logger.info("Shutdown signal received")

    # Cleanup
    api_task.cancel()
    if tg_app.updater.running:
        await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    scheduler.shutdown(wait=False)
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
else:
    # backward compat: `from bot import main; main()` still works via bot/__init__.py
    asyncio.run(main())
