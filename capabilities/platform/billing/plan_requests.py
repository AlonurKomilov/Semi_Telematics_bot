"""Telling someone a customer asked for a plan we do not sell self-serve.

Two channels, deliberately unequal:

- **Telegram to the operators is the one that must work.** It needs no
  configuration beyond ``SYSTEM_OWNER_IDS``, which is already set on
  every deployment, and it reaches a person in seconds.
- **Email is best-effort.** It goes to ``SALES_EMAIL`` when that is set,
  and it is the channel a reply can come back on — but a platform whose
  mail is not configured must still not swallow a customer's request.

Neither may raise: a notification that fails has to leave the REQUEST
standing, because the row is the thing the operator can still find.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def sales_inbox() -> str:
    """Where a request is emailed, '' when nobody configured one."""
    return (os.getenv("SALES_EMAIL") or "").strip()


def _lines(req: dict, account_name: str) -> list[str]:
    return [
        f"Case {req.get('case_number') or '(unnumbered)'}",
        f"Account: {account_name} (#{req.get('account_id')})",
        f"Plan asked for: {req.get('tier')}",
        f"Reply to: {req.get('contact_email') or '(none given)'}",
        "",
        (req.get("note") or "(no message)"),
    ]


async def notify_operators(account_id: int, req: dict, account_name: str) -> int:
    """Telegram, to whoever holds SYSTEM_OWNER_IDS.

    The bot handle comes from the registry the rest of the API uses —
    the API process has no bot of its own. Returns how many operators
    were reached; zero is worth logging and never worth raising, because
    the REQUEST is already recorded and that is the thing an operator
    can still find.
    """
    from capabilities.permissions import roles
    ids = getattr(roles, "SYSTEM_OWNER_IDS", set()) or set()
    text = "<b>📣 Plan request</b>\n\n" + "\n".join(_lines(req, account_name))
    # The operators are on the SYSTEM bot, not the customer's own — a
    # request about OUR pricing is ours to answer. Fall back to the
    # account's bot only if no system app is registered, which is better
    # than dropping the message.
    bot_app = None
    try:
        from infra.bot_registry import get_app_for_account, get_system_app
        bot_app = get_system_app() or get_app_for_account(account_id)
    except Exception:
        logger.exception("plan request %s: no bot registry", req.get("case_number"))
    if bot_app is None or not ids:
        logger.warning("plan request %s had no operator to notify: %s",
                       req.get("case_number"), text)
        return 0
    sent = 0
    for tg_id in sorted(ids):
        try:
            await bot_app.bot.send_message(
                chat_id=tg_id, text=text, parse_mode="HTML",
                disable_web_page_preview=True)
            sent += 1
        except Exception as e:
            logger.warning("plan request to operator %s failed: %s", tg_id, e)
    return sent


def email_sales(req: dict, account_name: str) -> bool:
    """Best-effort copy to the sales inbox. False when there is nowhere
    to send it or the send failed — the caller carries on either way."""
    to = sales_inbox()
    if not to:
        logger.info("plan request %s not emailed: SALES_EMAIL is unset",
                    req.get("case_number"))
        return False
    try:
        from capabilities.email.smtp import send_email
        return bool(send_email(
            to=to,
            subject=f"[{req.get('case_number')}] {account_name} asks about the "
                    f"{req.get('tier')} plan",
            body="\n".join(_lines(req, account_name)),
        ))
    except Exception:
        logger.exception("plan request %s could not be emailed to %s",
                         req.get("case_number"), to)
        return False
