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
  1. infra.startup.initialize()  — encryption, DB, AI, Redis, key migration
  2. build_app()                — Telegram Application with all handlers (if ENABLE_BOT)
  3. APScheduler                — scheduled alert/report jobs (if ENABLE_SCHEDULER)
  4. run_api()                  — FastAPI uvicorn server (if ENABLE_API)
  5. run_bot()                  — Telegram polling or webhook (if ENABLE_BOT)
  6. await stop_event           — block until SIGINT/SIGTERM
  7. infra.startup.shutdown()    — Redis, DB, caches
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

import infra.startup                                # noqa: E402

if _ENABLE_BOT or _ENABLE_SCHEDULER:
    from telegram import Update                    # noqa: E402
    from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
    from interfaces.bot.app import build_app                      # noqa: E402
    from interfaces.bot.config import (                           # noqa: E402
        WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_SECRET, USE_WEBHOOK,
        logger,
    )
    from interfaces.bot.scheduler import register_all as _register_jobs  # noqa: E402
    from infra.bot_registry import init_registry, set_handler_setup  # noqa: E402
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
    if _ENABLE_API:
        mode_parts.append("API")
    if _ENABLE_BOT:
        mode_parts.append("Bot")
    if _ENABLE_SCHEDULER:
        mode_parts.append("Scheduler")
    logger.info("Starting 4truck — services: %s", "+".join(mode_parts) or "none")

    # ── 1. Platform infrastructure (always required) ─────────────
    await infra.startup.initialize()

    # ── Graceful shutdown wiring ─────────────────────────────────
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tg_app = None
    sys_tg_app = None  # Optional operator-only daemon — see system_app.py
    scheduler = None
    sched_lock_task = None
    registry = None
    api_task = None

    # ── 2. Bot setup ─────────────────────────────────────────────
    if _ENABLE_BOT:
        tg_app = build_app()

        # Per-account bot registry
        registry = init_registry(system_app=tg_app)
        try:
            from infra.platform import get_platform_db
            started = await registry.start_all(get_platform_db())
            if started:
                logger.info("Started %d per-account bot(s) from database", started)
        except Exception:
            logger.exception("Failed to start per-account bots (continuing with system bot)")

        # System / operator bot — separate daemon on TELEGRAM_BOT_TOKEN.
        # Skipped silently when the token isn't configured; operators
        # can use system.4truck.us instead.
        from interfaces.bot.system_app import build_system_app
        sys_tg_app = build_system_app()

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
            import infra.cache as _rcache
            # RETRY, never one-shot.  A hard crash (the Aug 3-7 native
            # deaths) leaves the lock un-released with up to 90s of TTL;
            # systemd restarts us 5s later, the single try lost, and the
            # process then ran WITHOUT ANY SCHEDULER until the next
            # manual restart — polling looked alive while every cron job
            # was dead (daily/weekly rollups silently missing Aug 6-9).
            lock_acquired = await _rcache.acquire_lock("scheduler:global", ttl_secs=90)
            _lock_waits = 0
            while not lock_acquired:
                if _lock_waits == 0:
                    logger.warning(
                        "Scheduler lock held (stale from a crashed "
                        "instance?) — retrying every 15s until acquired")
                _lock_waits += 1
                await asyncio.sleep(15)
                lock_acquired = await _rcache.acquire_lock(
                    "scheduler:global", ttl_secs=90)
            if lock_acquired:
                # misfire_grace_time defaults to ONE SECOND: a job whose
                # moment passes while the loop is busy — a deploy, a GC
                # pause, an 8-core box under a parallel test run — is
                # silently dropped rather than run late.  For hourly and
                # daily roll-ups that is a permanent hole, since a tier
                # only heals while its source rows survive.  Five minutes
                # of grace covers a restart; coalesce (set per job) keeps
                # a backlog from firing the same job repeatedly.
                # timezone="UTC" is LOAD-BEARING: without it APScheduler
                # falls back to the server's OS clock (Europe/Berlin on
                # this host), and every cron job registered without an
                # explicit tz fired two hours early — including the
                # account purge and the billing-metering "yesterday"
                # flush, whose UTC day math then closed the WRONG day.
                # Nine job comments claimed UTC; this makes them true.
                scheduler = AsyncIOScheduler(
                    timezone="UTC",
                    job_defaults={"misfire_grace_time": 300, "coalesce": True},
                )
                _register_jobs(scheduler, tg_app)
                scheduler.start()
                logger.info(
                    "Scheduler started (distributed lock acquired%s)",
                    f" after {_lock_waits} retries" if _lock_waits else "")

                # Keep the lock ALIVE while we hold it.  Without the
                # heartbeat it expired 90s after boot and only ever
                # guarded the first minute-and-a-half — after that a
                # second instance could have won it too.  On heartbeat
                # failure (Redis blip / expiry) re-acquire; both calls
                # fail open, so Redis being down never stops jobs.
                async def _scheduler_lock_heartbeat() -> None:
                    while True:
                        await asyncio.sleep(30)
                        try:
                            ok = await _rcache.heartbeat_lock(
                                "scheduler:global", ttl_secs=90)
                            if not ok:
                                await _rcache.acquire_lock(
                                    "scheduler:global", ttl_secs=90)
                        except Exception:
                            pass

                sched_lock_task = asyncio.create_task(
                    _scheduler_lock_heartbeat())

    # ── 4. API server ────────────────────────────────────────────
    if _ENABLE_API:
        api_task = asyncio.create_task(run_api())

        # Surface silent task crashes to the error reporter
        def _api_task_done(t: asyncio.Task) -> None:
            if not t.cancelled():
                exc = t.exception()
                if exc:
                    try:
                        from infra.error_reporter import report_error
                        asyncio.get_event_loop().create_task(
                            report_error(exc, source="task", job_name="run_api")
                        )
                    except Exception:
                        pass
        api_task.add_done_callback(_api_task_done)

    # ── 5. Start bot(s) ──────────────────────────────────────────
    if _ENABLE_BOT and tg_app:
        try:
            await run_bot(tg_app)
        except Exception as exc:
            logger.exception("Bot polling crashed")
            try:
                from infra.error_reporter import report_error
                await report_error(exc, source="task", job_name="run_bot")
            except Exception:
                pass

    # Operator-only system bot — independent polling loop on a
    # different token.  Lifecycle is the same shape as the customer
    # daemon but its handler set is just the /admin family, so it
    # doesn't share state with the customer or per-account bots.
    if _ENABLE_BOT and sys_tg_app:
        try:
            await run_bot(sys_tg_app)
            logger.info("System bot daemon started (TELEGRAM_BOT_TOKEN)")
        except Exception as exc:
            logger.exception("System bot polling crashed")
            try:
                from infra.error_reporter import report_error
                await report_error(exc, source="task", job_name="run_system_bot")
            except Exception:
                pass

    # ── 6. Wait for shutdown signal ──────────────────────────────
    await stop_event.wait()
    logger.info("Shutdown signal received")

    # ── 7. Cleanup ───────────────────────────────────────────────
    # Order matters.  Previously the scheduler shut down with
    # ``wait=False`` *after* the bots, so any in-flight scheduled job
    # (e.g. a ``check_events`` mid-fanout) lost the bot's HTTPX client
    # under its feet and the remaining subscribers got
    # ``RuntimeError('This HTTPXRequest is not initialized!')``.  That
    # explained the production symptom of videos arriving without the
    # follow-up text message when the service was restarted during a
    # delivery cycle.
    #
    # Drain the scheduler first (``wait=True``) so every job that was
    # already running gets to complete, then stop the bots — by that
    # point nothing else is calling ``bot.send_*``.

    if api_task:
        api_task.cancel()
    # Cancel BEFORE the drain loop below — an infinite heartbeat task
    # would otherwise hold the drain open for its full grace period.
    if sched_lock_task:
        sched_lock_task.cancel()

    if scheduler:
        # Stop accepting new jobs and let in-flight async tasks finish
        # naturally.  ``wait=False`` here is deliberate: the wait=True
        # path is *blocking* (it joins on a thread executor) and would
        # freeze the event loop while our async jobs are still inside
        # asyncio.gather.  We do the awaiting ourselves below.
        scheduler.shutdown(wait=False)

        # Drain running async jobs with a bounded grace period.  Long
        # enough to cover a normal events-check fanout (~30s) but
        # bounded so systemd's TimeoutStopSec can't be exceeded.  Tune
        # via SHUTDOWN_DRAIN_SEC env var; bump TimeoutStopSec in the
        # systemd unit to a few seconds more than this.
        drain_secs = int(os.getenv("SHUTDOWN_DRAIN_SEC", "40"))
        deadline = asyncio.get_event_loop().time() + drain_secs
        current = asyncio.current_task()
        # Pending async jobs are scheduled as separate tasks by
        # APScheduler.  ``all_tasks() - {current}`` yields everything
        # else we're sharing the loop with (API task already cancelled
        # above; bot polling tasks still running but those won't block).
        while True:
            pending = [
                t for t in asyncio.all_tasks()
                if t is not current and not t.done()
                and "Application.run_polling" not in repr(t)
            ]
            if not pending:
                break
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                logger.warning(
                    "Shutdown drain timeout (%ds) — %d task(s) still running",
                    drain_secs, len(pending),
                )
                break
            try:
                await asyncio.wait(pending, timeout=min(2.0, remaining))
            except Exception:
                break

        import infra.cache as _rcache
        await _rcache.release_lock("scheduler:global")

    if registry:
        await registry.stop_all()

    if tg_app:
        if tg_app.updater.running:
            await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()

    # Stop the operator daemon too if it was started.  Same teardown
    # shape; failures here are logged but don't block the rest of the
    # shutdown sequence.
    if sys_tg_app:
        try:
            if sys_tg_app.updater and sys_tg_app.updater.running:
                await sys_tg_app.updater.stop()
            await sys_tg_app.stop()
            await sys_tg_app.shutdown()
        except Exception:
            logger.exception("System bot shutdown error")

    # Platform shutdown (Redis, DB, caches)
    await infra.startup.shutdown()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
