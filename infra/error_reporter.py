"""Built-in error reporter — Telegram notifications + DB audit log.

Captures unhandled exceptions from all five error surfaces:
  - Scheduler jobs       (infra/isolation.py)
  - FastAPI routes       (interfaces/api/app.py global_exception_handler)
  - Bot handlers         (interfaces/bot/app.py PTB error_handler)
  - Silent async tasks   (run.py api_task done-callback)
  - Startup failures     (infra/startup.py — pre-DB, Telegram only)

Usage::

    # Once at startup (infra/startup.py):
    from infra.error_reporter import init_error_reporter
    init_error_reporter(bot_token, chat_id, platform_db)

    # Everywhere an exception is caught:
    from infra.error_reporter import report_error
    await report_error(exc, source="scheduler", job_name="fault_check", account_id=7)

The reporter NEVER raises — it is safe to call from inside any except block.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any, Optional
import weakref

logger = logging.getLogger(__name__)

# ── Module-level state (set once by init_error_reporter) ─────────

_bot_token: str = ""
_chat_id: str = ""
_db_ref: Any = None                          # weakref proxy to PlatformDB (or None)
_cooldown: dict[str, float] = {}             # in-memory rate-limit fallback
_COOLDOWN_SECS = 300                         # 5-minute window per (error_class + source)
_MAX_TB_CHARS = 3500                         # Telegram message cap is 4096; leave headroom


def init_error_reporter(bot_token: str, chat_id: str, db: Any) -> None:
    """Initialise the reporter.  Call once from infra/startup.py after DB is ready.

    Args:
        bot_token: Telegram bot token (uses the existing system bot).
        chat_id: Telegram chat ID to receive error notifications.
        db: PlatformDB instance — used to persist error_log rows.
             Stored as a weakref so the reporter never prevents GC.
    """
    global _bot_token, _chat_id, _db_ref
    _bot_token = bot_token or ""
    _chat_id = str(chat_id) if chat_id else ""
    try:
        _db_ref = weakref.proxy(db) if db is not None else None
    except TypeError:
        _db_ref = db  # plain ref if weakref not supported (e.g. mock in tests)
    if _chat_id:
        logger.info("Error reporter ready — notifications → chat %s", _chat_id)
    else:
        logger.info("Error reporter: no ERROR_REPORT_CHAT_ID set — DB-only mode")


# ── Public API ────────────────────────────────────────────────────

async def report_error(
    exc: Optional[BaseException],
    *,
    source: str = "unknown",
    job_name: Optional[str] = None,
    account_id: Optional[int] = None,
) -> None:
    """Capture an exception: rate-limit Telegram + always write DB row.

    Safe to fire-and-forget via asyncio.create_task().
    Never raises under any circumstances.
    """
    try:
        await _report(exc, source=source, job_name=job_name, account_id=account_id)
    except Exception as inner:
        # Last-resort fallback — reporter must not propagate errors
        logger.warning("error_reporter internal failure: %s", inner)


# ── Implementation ────────────────────────────────────────────────

async def _report(
    exc: Optional[BaseException],
    *,
    source: str,
    job_name: Optional[str],
    account_id: Optional[int],
) -> None:
    error_type = type(exc).__name__ if exc is not None else "UnknownError"
    error_msg  = str(exc) if exc is not None else "(no exception object)"

    # Full traceback — use the current exception context if exc is active
    tb_text = ""
    if exc is not None:
        tb_text = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).strip()

    # Rate-limit key: same error class + same source + account → one Telegram per 5 min
    # Include account_id so errors from different tenants are not collapsed together
    acct_part = str(account_id) if account_id is not None else "_"
    rate_key = f"errreport:{source}:{error_type}:{acct_part}"
    telegram_allowed = await _check_rate_limit(rate_key)

    # Always write DB row (even when rate-limited for Telegram)
    await _write_db(
        source=source,
        job_name=job_name,
        account_id=account_id,
        error_type=error_type,
        error_msg=error_msg,
        tb=tb_text,
    )

    if telegram_allowed:
        await _send_telegram(
            source=source,
            job_name=job_name,
            account_id=account_id,
            error_type=error_type,
            error_msg=error_msg,
            tb=tb_text,
        )


async def _check_rate_limit(rate_key: str) -> bool:
    """Return True if Telegram notification is allowed (not rate-limited)."""
    # Try Redis first
    try:
        from infra.cache import setex_flag, exists
        if await exists(rate_key):
            return False
        await setex_flag(rate_key, _COOLDOWN_SECS)
        return True
    except Exception:
        pass

    # Fallback: in-memory dict
    now = time.monotonic()
    last = _cooldown.get(rate_key, 0.0)
    if now - last < _COOLDOWN_SECS:
        return False
    _cooldown[rate_key] = now
    return True


async def _write_db(
    *,
    source: str,
    job_name: Optional[str],
    account_id: Optional[int],
    error_type: str,
    error_msg: str,
    tb: str,
) -> None:
    """Persist an error_log row.  Silently skips if DB not ready."""
    if _db_ref is None:
        return
    try:
        await _db_ref.log_error(
            source=source,
            job_name=job_name,
            account_id=account_id,
            error_type=error_type,
            error_msg=error_msg,
            traceback_text=tb,
        )
    except Exception as e:
        logger.debug("error_reporter: DB write failed: %s", e)


async def _send_telegram(
    *,
    source: str,
    job_name: Optional[str],
    account_id: Optional[int],
    error_type: str,
    error_msg: str,
    tb: str,
) -> None:
    """Send a Telegram notification via direct Bot API HTTP call.

    Uses httpx directly (no Application object) so it works before the bot
    is initialised and at shutdown time.
    """
    if not _bot_token or not _chat_id:
        return

    # Build message text — all user/dynamic values must be HTML-escaped
    header_parts = [f"🔴 <b>ERROR</b> — <code>{source}</code>"]
    if job_name:
        header_parts[0] += f" · <code>{_escape_html(job_name)}</code>"
    if account_id is not None:
        header_parts.append(f"account {account_id}")

    header = " · ".join(header_parts) if len(header_parts) > 1 else header_parts[0]

    tb_truncated = tb[-_MAX_TB_CHARS:] if len(tb) > _MAX_TB_CHARS else tb
    if tb_truncated and len(tb) > _MAX_TB_CHARS:
        tb_truncated = "…(truncated)\n" + tb_truncated

    body = f"<code>{_escape_html(error_type)}: {_escape_html(error_msg)}</code>"
    if tb_truncated:
        body += f"\n\n<pre>{_escape_html(tb_truncated)}</pre>"

    text = f"{header}\n\n{body}"

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{_bot_token}/sendMessage",
                json={
                    "chat_id": _chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
    except Exception as e:
        logger.debug("error_reporter: Telegram send failed: %s", e)


def _escape_html(text: str) -> str:
    """Minimal HTML escaping for <pre> blocks."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
