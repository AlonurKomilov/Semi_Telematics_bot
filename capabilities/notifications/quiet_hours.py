"""Quiet hours — the spine's "don't disturb this person right now" policy.

Part of the alert-DM migration (capabilities/alerting/docs/alert-dm-migration.md).
The spine owns the MECHANICS: which channels respect quiet hours, when
severity overrides them, deferring into the queue and flushing when the
window ends.  The RULE — *is this user quiet right now?* — belongs to
whoever owns user schedules, and is handed in as a registered provider
(the same one-way seam as categories / action handlers):

    from capabilities.notifications.quiet_hours import (
        register_quiet_hours_provider)
    register_quiet_hours_provider(my_async_rule)   # (account_id, user_id) -> bool

Semantics:

  • Only channels declaring ``respects_quiet_hours = True`` defer
    (``telegram_dm``, ``web_push`` — the ones that buzz a phone).  The
    inbox is silent by nature; email has its own cadence/digest system.
  • CRITICAL severity always bypasses — quiet hours mute noise, not
    emergencies (same rule the legacy DND path applies).
  • Deferred items ride the existing digest queue under the ``quiet``
    cadence; :func:`service.flush_quiet_deferrals` drains a recipient's
    items as ONE summary the moment the provider says they are no longer
    quiet (checked hourly).
  • FAIL-OPEN: no provider registered, or a provider error → not quiet →
    deliver.  A scheduling bug must degrade to a too-eager notification,
    never a silently swallowed one.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from capabilities.notifications.channels import SEVERITY_RANK

logger = logging.getLogger(__name__)

# The digest-queue cadence quiet deferrals ride under.  Not a member of
# DIGEST_CADENCES: the hourly/daily flushes must never drain it — its own
# flush checks the provider per recipient instead of a clock.
QUIET_DEFER_CADENCE = "quiet"

# Severity at or above this rank ignores quiet hours.
_BYPASS_RANK = SEVERITY_RANK["critical"]

QuietHoursProvider = Callable[[int, int], Awaitable[bool]]

_provider: QuietHoursProvider | None = None


def register_quiet_hours_provider(fn: QuietHoursProvider) -> QuietHoursProvider:
    """Register (last-wins) the async rule ``(account_id, user_id) ->
    is-quiet-now``.  One global provider: quiet hours is a property of
    the PERSON, not of any one event source."""
    global _provider
    _provider = fn
    return fn


def get_quiet_hours_provider() -> QuietHoursProvider | None:
    return _provider


def severity_bypasses_quiet(severity: str) -> bool:
    return SEVERITY_RANK.get(severity, 0) >= _BYPASS_RANK


async def is_recipient_quiet(account_id: int, user_id: int) -> bool:
    """Ask the registered provider; FAIL-OPEN (deliver) on no provider,
    bad ids, or provider error."""
    if _provider is None:
        return False
    try:
        return bool(await _provider(int(account_id), int(user_id)))
    except Exception as e:
        logger.error("quiet-hours provider failed for %s/%s: %s",
                     account_id, user_id, e)
        return False


async def defer_notification(
    db,
    account_id: int,
    *,
    user_id: int,
    address: str,
    category: str,
    line: str,
    severity: str = "info",
    channel: str = "telegram_dm",
) -> bool:
    """Queue ONE notification line for a quiet recipient — the source
    already decided "this person is quiet right now" (its own DND check)
    and hands the spine the deferred line instead of keeping a private
    queue.  Delivered by the hourly quiet flush as part of the
    recipient's off-shift summary (source digest renderer).  Returns
    False on enqueue failure — the caller decides whether to send live
    instead (a too-eager notification beats a lost one)."""
    try:
        await db.enqueue_digest_item(
            account_id, "user", str(user_id), channel,
            QUIET_DEFER_CADENCE, category, line, str(address or ""),
            severity=severity,
        )
        return True
    except Exception as e:
        logger.error("defer_notification failed (%s/%s %s): %s",
                     account_id, user_id, category, e)
        return False
