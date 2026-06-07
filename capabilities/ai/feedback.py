"""Implicit-satisfaction signals for the model router.

The router we shipped in e3a8f93 / 9531c5e scores each model on
availability + speed + tool success per (account x role x category).
What it never saw: whether the user actually *liked* the answer.

The cheapest dissatisfaction signal we can collect without a UI is
a **re-ask within N seconds**.  If a user sends a new message in
the chat within 30 s of receiving an AI response, the response
almost certainly didn't satisfy them — they came back to clarify,
re-phrase, or ask the same question differently.  We flag the
row that produced the unsatisfying response with ``had_reask=TRUE``
and the scorer folds it in as a 15 % weight (1 - reask_rate).

Why 30 s: industry data on conversational AI shows the median
"long enough to actually read the answer" time is ~25 s.  Anything
shorter is almost certainly a re-ask.  We pick 30 to give the
benefit of the doubt to fast readers.

Future signals (regenerate button, "no I meant…" phrasing detection,
session-ended-after-one-turn=satisfied) all plug into the same
column — they just need their own ``mark_…`` storage method.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("bot.ai")

# Threshold below which a follow-up message counts as a re-ask.
# A real reading-then-following-up flow is usually 30s+; anything
# faster signals "the previous answer didn't land".
_REASK_THRESHOLD_SEC = 30


async def detect_reask_and_mark(
    account_id: int,
    user_id: int,
    *,
    threshold_sec: int = _REASK_THRESHOLD_SEC,
) -> bool:
    """When this user's new chat request arrives within ``threshold_sec``
    of receiving the previous AI response, flip ``had_reask=TRUE`` on
    that response's ai_usage row.

    Returns True when a re-ask was detected and a row was marked.
    Best-effort: a DB hiccup logs at debug and returns False rather
    than blocking the chat call.  Idempotent — re-running on the
    same boundary just re-sets an already-TRUE flag.

    Designed to be called at the *start* of the chat request, before
    ``resolve_tier_for_request`` runs, so the marked row reflects
    the response the user is actually unsatisfied with (the prior
    one), not the one we're about to produce.
    """
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()

        # Most recent AI response time for this user.  ai_chat_history
        # rows are written *after* the AI replies, so the newest
        # role='model' row is the answer the user just read before
        # sending us a new message.
        cur = await pdb._db.execute(
            "SELECT created_at FROM ai_chat_history "
            "WHERE account_id = ? AND user_id = ? AND role = 'model' "
            "ORDER BY id DESC LIMIT 1",
            (account_id, user_id),
        )
        row = await cur.fetchone()
        if not row:
            return False  # First exchange with this user — no prior to compare

        prev_ts_str = str(row[0]) if row[0] else ""
        if not prev_ts_str:
            return False
        try:
            # ai_chat_history.created_at is a TEXT column stored as
            # ISO 8601 (see save_chat_messages in adapters/storage/ai_chat.py).
            # Postgres may serialize as ``YYYY-MM-DD HH:MM:SS+00`` instead
            # of full ISO — handle both shapes defensively.
            normalized = prev_ts_str.replace("Z", "+00:00")
            if " " in normalized and "T" not in normalized:
                normalized = normalized.replace(" ", "T", 1)
            prev_ts = datetime.fromisoformat(normalized)
            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug("Re-ask check: couldn't parse prev_ts %r", prev_ts_str)
            return False

        delta = datetime.now(timezone.utc) - prev_ts
        if delta > timedelta(seconds=threshold_sec):
            return False  # Long enough gap — assume the answer landed

        # Re-ask detected.  Flip had_reask on the row that produced
        # the response.
        updated = await pdb.mark_last_ai_usage_reask(account_id, user_id)
        if updated:
            logger.info(
                "Re-ask detected for user=%d (gap=%.1fs); marked 1 ai_usage row",
                user_id, delta.total_seconds(),
            )
        return bool(updated)
    except Exception as e:
        logger.debug("Re-ask detection skipped: %s", e)
        return False
