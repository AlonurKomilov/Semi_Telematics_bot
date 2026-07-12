"""AI retention — the AI capability owns its chat-history log.

``ai.chat_history`` -> physical table ``ai_chat_history`` (platform-scoped,
per-user AI assistant conversations).  Two caps compose: the storage mixin's
per-user 100-row cap bounds volume on every write; this 90-day window bounds
age from the nightly retention job — chat is personal free-form content, so
it shouldn't outlive its usefulness (scrollback + model follow-up context)
just because a user went quiet.
"""

from capabilities.data_lifecycle.retention.registry import (
    RetentionNeed,
    RetentionTarget,
    register_need,
    register_target,
)

register_target(RetentionTarget(
    "ai.chat_history", "AI assistant chat history", "platform",
    lambda db, _acct, days: db.prune_ai_chat_history(days=days),
))
register_need(RetentionNeed(
    "ai", "ai.chat_history", 90,
    "dashboard scrollback + model follow-up context; privacy age-cap",
))
