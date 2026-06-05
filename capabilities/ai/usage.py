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


async def record_call_attempt(
    *,
    account_id: int | None,
    user_id: int | None,
    role: str | None,
    action: str | None,
    model: str,
    latency_ms: int,
    error_type: str,
    usage: dict | None = None,
    tool_success_count: int | None = None,
) -> None:
    """Write one ai_usage row for a single model attempt.

    Called from inside the AI module after each model call (success or
    failure) so the router can compute real per-model error_rate /
    latency / tool_success_rate per (account, role).  Skips when the
    caller didn't pass account_id + action — that's the legacy entry
    path that has no router context.

    Lazy-imports platform_db so this module stays import-safe at
    package load time (capabilities.ai is imported before infra is
    fully initialised on cold start).
    """
    if account_id is None or not action:
        return
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        await pdb.log_ai_usage(
            account_id, user_id or 0, model, action,
            (usage or {}).get("prompt_tokens", 0),
            (usage or {}).get("reply_tokens", 0),
            (usage or {}).get("total_tokens", 0),
            (usage or {}).get("thinking_tokens", 0),
            latency_ms=latency_ms,
            error_type=error_type,
            tool_success_count=tool_success_count,
            role=role,
        )
    except Exception as e:
        logger.debug("AI attempt telemetry failed: %s", e)


def classify_error(exc: Exception) -> str:
    """Map an exception to one of the ai_usage.error_type buckets."""
    s = str(exc).lower()
    if "429" in s or "resource exhausted" in s:
        return "429"
    if "timeout" in s or "timed out" in s:
        return "timeout"
    if "safety" in s or "content filter" in s or "blocked" in s:
        return "content_filter"
    if "500" in s or "502" in s or "503" in s or "504" in s:
        return "5xx"
    if "empty" in s:
        return "empty"
    return "other"


async def log_ai_usage(
    ai_module,
    platform_db,
    account_id: int,
    user_id: int,
    action: str,
    usage: dict | None,
    *,
    latency_ms: int | None = None,
    error_type: str | None = None,
    tool_success_count: int | None = None,
    role: str | None = None,
    model: str | None = None,
) -> None:
    """Log AI token usage + router telemetry for an AI call.

    ``usage`` is the dict returned alongside the call's text result —
    pass it explicitly to avoid the cross-task races of the old
    ``get_last_usage()`` global.

    The router telemetry fields (``latency_ms``, ``error_type``,
    ``tool_success_count``, ``role``) feed ``get_ai_model_scores`` so
    the model picker can promote whichever in-tier model is actually
    fastest + most reliable for this role.

    Unlike before, **failure rows are also logged** (zero token counts
    with ``error_type='429'`` / ``'5xx'`` / etc.) so the router can
    compute a real error rate — previously the early-exit on missing
    usage meant every model looked perfect.

    ``model`` overrides the inferred model — pass it when the call
    fell over to a different model than the account default (fallback
    path) so the row is attributed to the model that actually ran.
    """
    has_usage = bool(usage)
    has_error = bool(error_type) and error_type != "ok"
    if not has_usage and not has_error:
        # Nothing meaningful to record.
        return

    model_name = (
        model
        or ai_module.get_account_model_name(account_id)
        or ai_module.get_current_model_name()
    )
    try:
        await platform_db.log_ai_usage(
            account_id, user_id, model_name, action,
            (usage or {}).get("prompt_tokens", 0),
            (usage or {}).get("reply_tokens", 0),
            (usage or {}).get("total_tokens", 0),
            (usage or {}).get("thinking_tokens", 0),
            latency_ms=latency_ms,
            error_type=error_type or ("ok" if has_usage else None),
            tool_success_count=tool_success_count,
            role=role,
        )
    except Exception as e:
        logger.debug("AI usage logging failed: %s", e)
