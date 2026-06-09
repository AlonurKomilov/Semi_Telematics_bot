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
import re
import unicodedata
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("bot.ai")

# Threshold below which a follow-up message counts as a re-ask.
# A real reading-then-following-up flow is usually 30s+; anything
# faster signals "the previous answer didn't land".
_REASK_THRESHOLD_SEC = 30

# Phrase prefixes that signal explicit dissatisfaction with the
# previous AI answer.  Anchored to the *start* of the user's
# message (after small connectors like "no, "/"hey, ") so bare
# matches inside a sentence ("there are no faults") don't trigger.
#
# Patterns are case-insensitive and accent-stripped before matching
# (see ``_normalize_for_match``) so "No, yo pregunté…" and "no, yo
# pregunte..." both fire.  Languages without phrase lists (am / fr /
# pa / so / uz) return False — silent no-op, never a KeyError.
#
# Adding a locale: drop a list under its 2-letter key.  Each entry is
# a raw substring (we anchor + boundary it at match time, not in
# the data, so phrases stay readable).
#
# False-positive guard: only match dissatisfaction-flavoured phrases
# ("you didn't answer", "not what I asked", "I asked about X"), NOT
# bare correction words like "actually" alone — "actually I want
# the 2023 model" is a legitimate refinement, not a complaint.
_DISSAT_PHRASES: dict[str, tuple[str, ...]] = {
    "en": (
        "no, that's not",
        "no thats not",
        "that's not what i",
        "thats not what i",
        "you didn't answer",
        "you didnt answer",
        "you did not answer",
        "i asked about",
        "i was asking about",
        "no i meant",
        "no, i meant",
        "no i mean",
        "wrong answer",
        "that's wrong",
        "thats wrong",
        "not what i asked",
        "you missed",
        "you didn't get",
        "you didnt get",
    ),
    "es": (
        "no, no es eso",
        "no es lo que",
        "no respondiste",
        "no contestaste",
        "te pregunte sobre",
        "te pregunte por",
        "no me respondes",
        "respuesta incorrecta",
        "eso esta mal",
    ),
    "ru": (
        "нет, я спрашивал",
        "нет я спрашивал",
        "ты не ответил",
        "вы не ответили",
        "я имел в виду",
        "я не об этом",
        "не то спросил",
        "это не ответ",
        "неправильный ответ",
    ),
    "uk": (
        "ні, я питав",
        "ні я питав",
        "ти не відповів",
        "ви не відповіли",
        "я мав на увазі",
        "я не про це",
        "неправильна відповідь",
    ),
}

# How many leading chars of the user's message to search.  Enough
# to allow short prefixes ("hey, no I meant…") without scanning the
# whole message — phrase-style dissatisfaction lands at the very
# start of the reply in real conversation.
_PHRASE_PREFIX_CHARS = 80


def _normalize_for_match(text: str) -> str:
    """Casefold + strip accents so Unicode variants match the ASCII
    phrase table.  ``casefold()`` is stricter than ``.lower()`` (it
    handles German ß, Turkish dotless i, etc.) and ``NFD`` decomposes
    e.g. 'é' into 'e' + combining accent which we then strip."""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


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


# Compile the boundary-anchored phrase regexes once at import.
# Each entry becomes ``(?:^|[\\s,.;:!?])phrase`` — phrase preceded by
# the start of the prefix window or a word boundary character — so
# bare "no" inside a sentence doesn't trigger but "no, that's not…"
# at the start, "hey, no I meant…" after a comma, etc. all do.
_DISSAT_PATTERNS: dict[str, re.Pattern] = {
    lang: re.compile(
        r"(?:^|[\s,.;:!?])(?:" + "|".join(re.escape(p) for p in phrases) + r")",
    )
    for lang, phrases in _DISSAT_PHRASES.items()
}


async def detect_phrase_dissatisfaction_and_mark(
    account_id: int,
    user_id: int,
    message: str,
    *,
    language: str = "en",
) -> bool:
    """When the user's new chat message opens with a dissatisfaction
    phrase ("no, that's not what I asked", "you didn't answer", "I
    asked about…", localised equivalents), flip ``had_reask`` on the
    previous response's ai_usage row — explicit content-based signal
    that complements the timing-based ``detect_reask_and_mark``.

    Returns True when a phrase fired and a row was marked.  Best-
    effort: a DB hiccup logs at debug and returns False rather than
    blocking the chat call.  Idempotent against the same prior row.

    Designed to be called at the *start* of the chat request (sibling
    to ``detect_reask_and_mark``), so the marked row is the prior
    turn's answer the user is complaining about — not the one we're
    about to produce.

    Languages without a phrase list silently return False — this
    matters for am / fr / pa / so / uz users whose dissatisfaction
    will only be captured via the timing signal until phrase tables
    are translated.
    """
    if not message:
        return False
    pattern = _DISSAT_PATTERNS.get(language)
    if pattern is None:
        # No phrase list for this locale yet — graceful no-op.
        return False
    normalized = _normalize_for_match(message[:_PHRASE_PREFIX_CHARS])
    if not pattern.search(normalized):
        return False
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        updated = await pdb.mark_last_ai_usage_reask(account_id, user_id)
        if updated:
            logger.info(
                "Phrase dissatisfaction detected for user=%d (lang=%s); "
                "marked 1 ai_usage row",
                user_id, language,
            )
        return bool(updated)
    except Exception as e:
        logger.debug("Phrase dissatisfaction marking skipped: %s", e)
        return False


async def flip_last_response_as_reask(
    account_id: int,
    user_id: int,
) -> bool:
    """Mark the user's most recent successful response as ``had_reask=TRUE``.

    Powers the dashboard's "Regenerate" button: the click itself is
    the dissatisfaction signal — no timing check, no phrase parsing.
    The user explicitly told us "this answer wasn't good", we trust
    it directly.

    Same target row as ``detect_reask_and_mark`` (latest
    ``request_type='question'`` + ``error_type='ok'`` row), so the
    scorer's math stays consistent across all three signals.
    """
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        updated = await pdb.mark_last_ai_usage_reask(account_id, user_id)
        if updated:
            logger.info(
                "Regenerate click for user=%d; marked 1 ai_usage row",
                user_id,
            )
        return bool(updated)
    except Exception as e:
        logger.debug("Regenerate-flip skipped: %s", e)
        return False


async def flip_last_response_as_thumbs_down(
    account_id: int,
    user_id: int,
    *,
    reason: str | None = None,
    note: str | None = None,
) -> bool:
    """Mark the user's most recent successful response as ``had_reask=TRUE``.

    Powers the dashboard's "thumbs-down" icon — semantically same
    as the regenerate click (explicit dissatisfaction), but doesn't
    re-fire the prompt.

    Optional ``reason`` + ``note`` capture the "Why?" follow-up
    form: a single-pick category (inaccurate / off_topic /
    incomplete / hallucinated / vague / unjust_refusal / other) and
    an optional free-text note.  When both are None the call
    behaves exactly like a bare thumbs-down (just flips
    ``had_reask``).  When reason is provided, the same row is
    flagged AND the reason is recorded — supplementary detail for
    per-category analytics, not a change to the scorer math.

    Shares the ``had_reask`` column with the timing / phrase /
    regenerate signals — all four converge on the idempotent
    ``mark_last_ai_usage_reask`` (or ``mark_last_ai_usage_feedback``
    when reason is set) so a user who does multiple of them on the
    same row still ends up with one ``had_reask=TRUE`` flag.
    """
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        if reason is None and note is None:
            updated = await pdb.mark_last_ai_usage_reask(account_id, user_id)
        else:
            updated = await pdb.mark_last_ai_usage_feedback(
                account_id, user_id, reason=reason, note=note,
            )
        if updated:
            logger.info(
                "Thumbs-down click for user=%d (reason=%s); marked 1 ai_usage row",
                user_id, reason or "—",
            )
        return bool(updated)
    except Exception as e:
        logger.debug("Thumbs-down-flip skipped: %s", e)
        return False


async def flip_last_response_as_thumbs_up(
    account_id: int,
    user_id: int,
) -> bool:
    """Mark the user's most recent successful response as
    ``had_thumbs_up=TRUE``.

    Powers the dashboard's "thumbs-up" icon — the only EXPLICIT
    POSITIVE signal in the scoring stack.  Every other signal in
    the satisfaction term is negative; this one pushes satisfaction
    *up* (capped at 1.0 in ``_compute_score``).

    Same target row shape as the reask helpers (latest
    ``request_type='question'`` + ``error_type='ok'``), so positive
    and negative signals key on the same per-attempt rows the
    router scores.
    """
    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        updated = await pdb.mark_last_ai_usage_thumbs_up(account_id, user_id)
        if updated:
            logger.info(
                "Thumbs-up click for user=%d; marked 1 ai_usage row",
                user_id,
            )
        return bool(updated)
    except Exception as e:
        logger.debug("Thumbs-up-flip skipped: %s", e)
        return False


# ── Heuristic refusal detector ────────────────────────────────────
#
# Catches the failure mode where the AI refuses a question that an
# available tool could have answered.  Recurring example: user asks
# "what vehicle was stopped 3 days without driving?", AI answers
# "I only have real-time data" without calling get_idle_vehicles.
#
# Pure regex + keyword match — no LLM call, microsecond latency.
# Deliberately covers only refusal-when-tool-existed.  Failure
# modes that need actual answer comprehension (hallucinated
# numbers, wrong-truck answers, scope mismatches) would require
# a model-based judge instead, which costs roughly as many tokens
# per turn as the original chat call.  This detector is the cheap
# half of the quality stack — leaves the expensive half off the
# table until evidence shows heuristics aren't enough.

# Refusal-flavoured opening phrases, by locale.  The detector
# checks if any of these substrings appear in the answer's first
# ~200 chars — refusals land at the start of the reply, not buried
# in a multi-paragraph response.  Phrases overlap with what
# real models actually emit; tested against the production gemini
# answers that triggered the original idle-vehicles bug.
_REFUSAL_PHRASES_BY_LANG: dict[str, tuple[str, ...]] = {
    "en": (
        "i don't have",
        "i do not have",
        "i only have",
        "i can only access",
        "i can only see",
        "i cannot access",
        "i can't access",
        "no historical data",
        "no access to",
        "i can't determine",
        "i cannot determine",
        "i'm unable to",
        "i am unable to",
        "i don't have access",
        "i lack access",
        "that data isn't available",
        "that information isn't available",
        "i don't have that data",
        "i don't have that information",
        "this isn't something i can",
    ),
    "es": (
        "no tengo acceso",
        "no puedo acceder",
        "no dispongo de",
        "no tengo datos",
        "no tengo información",
        "no me es posible",
    ),
    "ru": (
        "у меня нет",
        "я не имею доступа",
        "не имею данных",
        "я не могу определить",
        "я не имею",
        "у меня отсутствует",
    ),
    "uk": (
        "у мене немає",
        "не маю доступу",
        "я не можу визначити",
    ),
}

# Question keywords → tool that probably should have been called.
# Lists are deliberately tight to avoid false positives — a question
# containing "fuel" matches many tools; we don't try to disambiguate
# fuel-status (snapshot) vs fuel-cost (tool).  Only phrases that
# unambiguously imply a historical / tool-only data source go here.
#
# Adding a new tool: drop its keywords here.  Missing entries just
# mean the detector won't catch refusals for that tool — the cost
# of being conservative is silent misses, not false positives, so
# err toward fewer keywords if unsure.
_TOOL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "get_idle_vehicles": (
        "stopped", "not driving", "without driving", "haven't moved",
        "hasn't moved", "long-idle", "long idle", "sitting", "parked for",
        "off the road", "out of service", "been parked",
        "no se ha movido", "está parado", "не двигалс",
    ),
    "get_alert_history": (
        "previous alerts", "past alerts", "alert history",
        "alerts last week", "alerts yesterday", "old alerts",
        "earlier alerts",
    ),
    "get_recent_work_orders": (
        "shop visit", "service history", "repair history",
        "work order", "maintenance done", "past repair",
    ),
    "get_recent_inspections": (
        "inspection history", "previous inspection",
        "past inspection", "dvir", "pti history",
    ),
    "get_vehicle_history": (
        "history of truck", "vehicle history",
        "past status", "history for truck",
    ),
    "get_driver_hos_status": (
        "hours of service", "hos status",
        "off duty", "drive time", "remaining drive",
    ),
    "get_driver_scorecard": (
        "my driving", "how was my driving", "how am i driving",
        "scorecard", "driving score", "safety score",
        "my safety score", "my score",
    ),
    "get_driver_efficiency": (
        "my efficiency", "how efficient", "my mpg",
        "fuel efficiency", "eco driving", "eco-driving",
    ),
}


def _looks_like_refusal(answer: str, language: str) -> bool:
    """Detect a refusal-flavoured opening in the AI's reply.

    Only inspects the FIRST ~200 chars — real refusals land at the
    start of the reply, not after several paragraphs of content.
    Falls back to English phrases when the locale isn't in
    ``_REFUSAL_PHRASES_BY_LANG`` so non-EN users still get the check.
    """
    if not answer:
        return False
    head = _normalize_for_match(answer[:200])
    phrases = (
        _REFUSAL_PHRASES_BY_LANG.get(language)
        or _REFUSAL_PHRASES_BY_LANG["en"]
    )
    return any(p in head for p in phrases)


def _tools_matching_question(question: str) -> set[str]:
    """Return the set of tool names whose keyword set matches the
    user's question (case + accent insensitive).  Empty set when no
    tool's keywords are present in the question — i.e. it's likely a
    legitimate refusal, no tool would have helped."""
    if not question:
        return set()
    q = _normalize_for_match(question)
    matched: set[str] = set()
    for tool_name, keywords in _TOOL_KEYWORDS.items():
        if any(_normalize_for_match(k) in q for k in keywords):
            matched.add(tool_name)
    return matched


async def detect_unjustified_refusal_and_mark(
    account_id: int,
    user_id: int,
    question: str,
    answer: str,
    tool_results: list[dict] | None,
    *,
    language: str = "en",
) -> bool:
    """When the AI refused a question that a tool could have answered
    but didn't call, flip ``had_reask`` on this row.

    Three conditions must ALL hold for a flag:
    1. The answer opens with a refusal phrase (locale-aware).
    2. The user's question contains keywords mapping to one or more
       tools (from ``_TOOL_KEYWORDS``).
    3. NONE of those matching tools were actually called this turn
       (checked via the ``tool_results`` list from ``ask_agent``).

    A refusal where a relevant tool WAS called and just returned an
    error is legitimate — don't penalise the model for honest tool
    failures.

    Returns True when a problematic refusal was detected and flagged.
    Best-effort and idempotent (same semantics as the other
    ``*_and_mark`` helpers in this module).

    Designed to be called by the chat handlers AFTER ask_agent
    returns, so the row this flips is the per-attempt row that
    produced *this* turn's response — caught immediately, not a turn
    later like the timing/phrase detectors which target the prior
    turn.
    """
    if not _looks_like_refusal(answer, language):
        return False

    relevant = _tools_matching_question(question)
    if not relevant:
        # Refusal language but no tool would have helped — legitimate.
        return False

    tools_called = {tr.get("tool") for tr in (tool_results or [])}
    if relevant & tools_called:
        # A relevant tool WAS called; refusal is honest (tool errored
        # or returned empty).  Don't penalise the model.
        return False

    try:
        from infra.platform import get_platform_db
        pdb = get_platform_db()
        updated = await pdb.mark_last_ai_usage_reask(account_id, user_id)
        if updated:
            logger.info(
                "Unjustified refusal detected for user=%d "
                "(refused but %s could have answered); marked 1 row",
                user_id, sorted(relevant),
            )
        return bool(updated)
    except Exception as e:
        logger.debug("Unjustified-refusal marking skipped: %s", e)
        return False
