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

    Returns a dict with keys: name, role, vehicle_num, timezone.
    """
    role_val = user_obj.role.value if hasattr(user_obj.role, "value") else user_obj.role
    return {
        "name": getattr(user_obj, "display_name", "") or "",
        "role": role_val,
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
    prompt_category: str | None = None,
) -> None:
    """Write one ai_usage row for a single model attempt.

    Called from inside the AI module after each model call (success or
    failure) so the router can compute real per-model error_rate /
    latency / tool_success_rate per (account, role, prompt_category).
    Skips when the caller didn't pass account_id + action — that's
    the legacy entry path that has no router context.

    ``prompt_category`` sub-classifies free-form questions (lookup /
    analysis / comparison / summary / troubleshooting / other) so the
    router can prefer the model that's historically been best at
    *this kind* of question, not just any question on this role.

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
            prompt_category=prompt_category,
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


# ── Prompt category classifier ───────────────────────────────────
#
# Heuristic regex classifier — no LLM call, ~50µs per prompt.  Used
# by the router to score models per (account, role, prompt_category)
# so a model that wins at lookups doesn't have to also win at
# multi-step analysis to get picked.  Six buckets, more specific
# patterns checked first so "compare X vs Y" wins over "show me X
# vs Y".  Buckets and their telltale signals:
#
#   comparison      "compare", "vs", "versus", "difference between",
#                   "which is better", "X or Y"
#   troubleshooting "diagnose", "troubleshoot", "fault", "problem",
#                   "issue", "broken", "not working", "error"
#   summary         "briefing", "summary", "overview", "status report",
#                   "morning report", "give me a recap"
#   analysis        "why", "analyze", "explain", "reason", "trend",
#                   "predict", "root cause", "what's happening"
#   lookup          "show", "list", "where", "who", "what is", "which",
#                   "find", "tell me about"
#   other           anything that doesn't match above
#
# Tuned to fleet-domain phrasing — adjust patterns when the model
# starts misrouting questions in production.

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("comparison", re.compile(
        r"\bcompar(?:e|ing|ison)\b|"
        r"\bvs\.?\b|\bversus\b|"
        r"\bdifference between\b|"
        r"\bwhich is (?:better|best|faster|cheaper|safer|worse)\b|"
        r"\b\w+ or \w+\?",
        re.IGNORECASE,
    )),
    ("troubleshooting", re.compile(
        r"\bdiagnos(?:e|is|tic)\b|"
        r"\btroubleshoot\b|"
        r"\bfault\b|\bproblem\b|\bissue\b|\bbroken\b|\bnot working\b|"
        r"\berror code\b|\bdtc\b|\bspn\b",
        re.IGNORECASE,
    )),
    ("summary", re.compile(
        r"\bbriefing\b|\bsummary\b|\boverview\b|"
        r"\bstatus report\b|\bmorning report\b|"
        r"\b(?:give|show) me (?:a |the )?(?:recap|summary|briefing|status)\b|"
        r"\bsum it up\b|\btl;dr\b|"
        # "How was my driving today / this week / last month" — past
        # period recap of a single subject; clearly summary-shaped.
        r"\bhow was my\b|\bhow has my\b|\bhow have my\b|"
        # "How are my vehicles doing today" — present-state recap.
        r"\bhow (?:are|is) (?:my|the) \w+ doing\b",
        re.IGNORECASE,
    )),
    ("analysis", re.compile(
        r"\bwhy\b|"
        r"\banaly(?:s|z)e\b|\banalysis\b|"
        r"\bexplain\b|\breason\b|"
        r"\btrend\b|\bpattern\b|"
        r"\bpredict\b|\bforecast\b|"
        r"\broot cause\b|"
        r"\bwhat'?s? happening\b|"
        r"\bhow come\b",
        re.IGNORECASE,
    )),
    ("lookup", re.compile(
        r"\bshow\b|\blist\b|\bwhere\b|\bwho\b|"
        r"\bwhat (?:is|are|vehicle|truck|driver)\b|\bwhich\b|"
        r"\bfind\b|\btell me about\b|"
        r"\bhow many\b|\bhow much\b|"
        # "did you know what …" — interrogative lookup; user is
        # asking the assistant to surface a specific data point.
        r"\bdid you know\b|"
        # Standalone "show", "stopped N days", parameterised refinements.
        # These come up in follow-up turns ("3 days") which contain no
        # other lookup keywords — match them so the classifier doesn't
        # bucket follow-ups as 'other'.
        r"\bstopped\b|\bparked\b|\bnot driving\b|\bwithout driving\b|"
        r"\bidle\b",
        re.IGNORECASE,
    )),
)


def classify_prompt(text: str) -> str:
    """Bucket *text* into one of the prompt-category labels.

    Returns ``"other"`` when no pattern matches — that's the "I
    couldn't tell" bucket; the router uses it as its own scoring key
    so unmatched prompts still get their own slice of telemetry
    rather than polluting the lookup bucket with non-lookups.
    """
    if not text:
        return "other"
    # Truncate to keep the regex pass cheap on huge prompts (rare for
    # chat, but defensive against pathological inputs).
    sample = text[:500]
    for label, pat in _CATEGORY_PATTERNS:
        if pat.search(sample):
            return label
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
    prompt_category: str | None = None,
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
            prompt_category=prompt_category,
        )
    except Exception as e:
        logger.debug("AI usage logging failed: %s", e)
