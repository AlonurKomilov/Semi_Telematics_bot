"""AI usage utilities — Single Source of Truth for usage logging and user context.

Both bot and API interfaces import from here instead of maintaining
their own copies of these helpers.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def parse_ai_suggestions(text: str) -> tuple[str, list[str]]:
    """Extract '>> ...' suggestion lines from AI response text.

    Returns (clean_text, suggestions_list).
    """
    lines = text.split("\n")
    clean_lines: list[str] = []
    suggestions: list[str] = []
    for line in lines:
        m = re.match(r"^>>\s*(.+)$", line.strip())
        if m:
            suggestions.append(m.group(1).strip())
        else:
            clean_lines.append(line)
    return "\n".join(clean_lines).strip(), suggestions


def build_user_ai_context(user_obj) -> dict:
    """Build the ``user_context`` dict passed to AI generation functions.

    Accepts a DB user object (ORM row or dataclass).  Normalises role
    to its string value regardless of whether it is an enum or plain str.

    Returns a dict with keys: name, role, department, vehicle_num, timezone.
    """
    role_val = user_obj.role.value if hasattr(user_obj.role, "value") else user_obj.role
    return {
        "name": getattr(user_obj, "display_name", "") or "",
        "role": role_val,
        "department": getattr(user_obj, "department", "general") or "general",
        "vehicle_num": getattr(user_obj, "truck_num", None) or "",
        "timezone": getattr(user_obj, "timezone", "America/New_York") or "America/New_York",
    }


async def log_ai_usage(
    ai_module,
    platform_db,
    account_id: int,
    user_id: int,
    action: str,
    usage: dict | None,
) -> None:
    """Log AI token usage for an AI call.

    ``usage`` is the dict returned alongside the call's text result —
    pass it explicitly to avoid the cross-task races of the old
    ``get_last_usage()`` global.  Silently skips on no-usage / errors.

    ``ai_module`` is still passed in for the ``get_account_model_name``
    / ``get_current_model_name`` model-label lookup.
    """
    if not usage:
        return
    model_name = (
        ai_module.get_account_model_name(account_id)
        or ai_module.get_current_model_name()
    )
    try:
        await platform_db.log_ai_usage(
            account_id, user_id, model_name, action,
            usage.get("prompt_tokens", 0),
            usage.get("reply_tokens", 0),
            usage.get("total_tokens", 0),
            usage.get("thinking_tokens", 0),
        )
    except Exception as e:
        logger.debug("AI usage logging failed: %s", e)
