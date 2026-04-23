#!/usr/bin/env python3
"""Entry point for 4truck Bot + FastAPI API server.

Service-split flags (set via environment variables or .env):
  ENABLE_API=1       — start FastAPI/uvicorn on API_PORT (default 8000)
  ENABLE_BOT=1       — start Telegram bot (polling or webhook)
  ENABLE_SCHEDULER=1 — start APScheduler background jobs

Defaults: all three enabled (backward-compatible with the existing systemd unit).
To run only the API: ENABLE_BOT=0 ENABLE_SCHEDULER=0 ENABLE_API=1
To run only bot+scheduler: ENABLE_API=0 ENABLE_BOT=1 ENABLE_SCHEDULER=1

Startup order:
  1. core.startup.initialize()  — encryption, DB, AI, Redis, key migration
  2. build_app()                — Telegram Application with all handlers (if ENABLE_BOT)
  3. APScheduler                — scheduled alert/report jobs (if ENABLE_SCHEDULER)
  4. run_api()                  — FastAPI uvicorn server (if ENABLE_API)
  5. run_bot()                  — Telegram polling or webhook (if ENABLE_BOT)
  6. await stop_event           — block until SIGINT/SIGTERM
  7. core.startup.shutdown()    — Redis, DB, caches
"""

import asyncio
import os
import signal

from dotenv import load_dotenv
load_dotenv()

# ── Service flags ─────────────────────────────────────────────────
# Each defaults to "1" (enabled) so existing deployments need no changes.
_ENABLE_API       = os.getenv("ENABLE_API",       "1") == "1"
_ENABLE_BOT       = os.getenv("ENABLE_BOT",       "1") == "1"
_ENABLE_SCHEDULER = os.getenv("ENABLE_SCHEDULER", "1") == "1"

import core.startup                                # noqa: E402

if _ENABLE_BOT or _ENABLE_SCHEDULER:
    from telegram import Update                    # noqa: E402
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
    from interfaces.bot.app import build_app                      # noqa: E402
    from interfaces.bot.config import (                           # noqa: E402
        WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_SECRET, USE_WEBHOOK,
        logger,
    )
    from interfaces.bot.scheduler import register_all as _register_jobs  # noqa: E402
    from core.bot_registry import init_registry, set_handler_setup  # noqa: E402
    from interfaces.bot.handler_setup import register_handlers as _bot_handlers  # noqa: E402
    set_handler_setup(_bot_handlers)
else:
    import logging
    logger = logging.getLogger(__name__)


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
        from interfaces.api.app import create_api
    except ImportError:
        logger.info("FastAPI/uvicorn not installed — API server disabled")
        return

    api = create_api()
    api_port = int(os.environ.get("API_PORT", "8000"))
    config = uvicorn.Config(api, host="0.0.0.0", port=api_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    mode_parts = []
    if _ENABLE_API:       mode_parts.append("API")
    if _ENABLE_BOT:       mode_parts.append("Bot")
    if _ENABLE_SCHEDULER: mode_parts.append("Scheduler")
    logger.info("Starting 4truck — services: %s", "+".join(mode_parts) or "none")

    # ── 1. Platform infrastructure (always required) ─────────────
    await core.startup.initialize()

    # ── Graceful shutdown wiring ─────────────────────────────────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tg_app = None
    scheduler = None
    registry = None
    api_task = None

    # ── 2. Bot setup ─────────────────────────────────────────────
    if _ENABLE_BOT:
        tg_app = build_app()

        # Per-account bot registry
        registry = init_registry(system_app=tg_app)
        try:
            from core.platform import get_platform_db
            started = await registry.start_all(get_platform_db())
            if started:
                logger.info("Started %d per-account bot(s) from database", started)
        except Exception:
            logger.exception("Failed to start per-account bots (continuing with system bot)")

    # ── 3. Scheduler setup ───────────────────────────────────────
    if _ENABLE_SCHEDULER:
        if tg_app is None:
            # Scheduler needs bot_app to send messages — log and skip if no bot
            logger.warning(
                "ENABLE_SCHEDULER=1 requires ENABLE_BOT=1 (scheduler sends Telegram messages). "
                "Scheduler will not start."
            )
        else:
            # Acquire distributed lock so only one scheduler instance runs across deploys
            import adapters.cache.redis as _rcache
            lock_acquired = await _rcache.acquire_lock("scheduler:global", ttl_secs=90)
            if lock_acquired:
                scheduler = AsyncIOScheduler()
                _register_jobs(scheduler, tg_app)
                scheduler.start()
                logger.info("Scheduler started (distributed lock acquired)")
            else:
                logger.info("Scheduler lock held by another instance — this instance will not run jobs")

    # ── 4. API server ────────────────────────────────────────────
    if _ENABLE_API:
        api_task = asyncio.create_task(run_api())

    # ── 5. Start bot ─────────────────────────────────────────────
    if _ENABLE_BOT and tg_app:
        await run_bot(tg_app)

    # ── 6. Wait for shutdown signal ──────────────────────────────
    await stop_event.wait()
    logger.info("Shutdown signal received")

    # ── 7. Cleanup ───────────────────────────────────────────────
    if api_task:
        api_task.cancel()

    if registry:
        await registry.stop_all()

    if tg_app:
        if tg_app.updater.running:
            await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

    if scheduler:
        scheduler.shutdown(wait=False)
        import adapters.cache.redis as _rcache
        await _rcache.release_lock("scheduler:global")

    # Platform shutdown (Redis, DB, caches)
    await core.startup.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
