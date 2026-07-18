"""Mode-aware alert routing resolver.

Picks the Telegram chat(s) an alert should land in, given the account's
``alert_routing_mode``.  Single source of truth so ``pipeline.py``'s
existing dispatch paths don't have to branch on the mode in two
different places.

Modes:

  • ``single_group`` (legacy, default for every existing account) —
    one forum chat per account with a topic per alert_type.  Returns
    the single (chat_id, message_thread_id) target from
    ``alert_routing``.  This preserves the pre-Phase-3 behavior
    byte-for-byte; the only added overhead is one accounts-table read.

  • ``per_persona_groups`` (opt-in via accounts.alert_routing_mode) — flat group per
    persona.  Returns a list of targets:
      – primary persona group (chat_id, thread=None)
      – owner_admin aggregate group for severity == 'critical' only

    The owner_admin aggregate post is the cross-cutting digest: it
    fires on every CRITICAL regardless of which operational persona
    also got it.  Non-critical alerts skip the aggregate to keep the
    owner group from drowning in geofence pings.

Falls back from ``per_persona_groups`` → legacy ``single_group`` lookup
when the new tables aren't populated yet — useful during the staged
admin-UI rollout where an operator flips the mode flag before they've
finished configuring every persona's group.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from infra.platform import get_platform_db

from . import persona_mapping

logger = logging.getLogger(__name__)

# Severity that triggers the owner_admin aggregate cross-post and the
# @-mention path.  Matches the value pipeline.send_alert() / escalation
# use everywhere else — kept here as a local constant so the resolver
# is testable without importing the alerting pipeline module.
CRITICAL_SEVERITY = "critical"


@dataclass(frozen=True)
class AlertTarget:
    """One Telegram destination for an alert post.

    ``message_thread_id`` is ``None`` for flat persona-group posts
    (per_persona_groups mode) and an int for the legacy topic-threaded
    posts (single_group mode).  ``is_aggregate`` is True for the
    owner_admin cross-post so the pipeline can label / dedupe it
    separately (e.g. skip the @-mention here because the on-shift
    mentions only belong on the persona group, not on the owner
    digest).
    """
    chat_id: int
    message_thread_id: Optional[int]
    is_aggregate: bool = False
    persona: str = ""  # informational; empty for legacy single_group


async def resolve_alert_targets(
    *,
    account_id: int,
    alert_type: str,
    severity: str = "",
) -> list[AlertTarget]:
    """Return the targets ``post_alert_to_topic`` / ``send_alert``
    should fan out to.  Empty list ⇒ no group routing configured ⇒
    caller falls back to the legacy per-user DM loop (unchanged).

    Severity is normalised to lowercase before the critical check so
    the caller can pass whatever case the pipeline used internally.

    Crash-safe contract: any DB exception inside the resolver is
    logged and converted to an empty target list.  The pipeline never
    sees a routing-layer exception bubble up — a transient DB blip
    cannot take the alert dispatch down.
    """
    try:
        db = get_platform_db()
        mode = await _read_routing_mode(db, account_id)
        sev = (severity or "").strip().lower()

        if mode == "per_persona_groups":
            targets, primary_present = await _resolve_per_persona(
                db, account_id, alert_type, sev,
            )
            # Only short-circuit to per-persona targets when the PRIMARY
            # persona group is registered.  An aggregate-only result
            # (owner_admin configured but the operational persona for
            # this alert isn't) means the migration is half-done; fall
            # back to legacy so non-CRITICAL traffic — which never gets
            # the aggregate — and CRITICAL traffic agree on the same
            # destination during staged onboarding.
            if primary_present:
                return targets

        return await _resolve_single_group(db, account_id, alert_type)
    except Exception as e:
        logger.warning(
            "routing_resolver failed acct=%d type=%s sev=%s — "
            "returning [] so the pipeline falls back to DM fanout: %s",
            account_id, alert_type, severity, e,
        )
        return []


async def _read_routing_mode(db, account_id: int) -> str:
    """Read the account's mode.  Defaults to 'single_group' on any error
    so a transient DB blip can't accidentally re-route an alert."""
    try:
        account = await db.get_account(account_id)
    except Exception as e:
        logger.warning(
            "routing_resolver: get_account failed acct=%d — "
            "defaulting to single_group: %s",
            account_id, e,
        )
        return "single_group"
    if account is None:
        return "single_group"
    return getattr(account, "alert_routing_mode", "single_group") or "single_group"


async def _resolve_per_persona(
    db, account_id: int, alert_type: str, sev: str,
) -> tuple[list[AlertTarget], bool]:
    """Per-persona-groups path.  Returns ``(targets, primary_present)``:

      • ``targets`` — the AlertTarget list to fan out to
      • ``primary_present`` — True when the operational persona for
        ``alert_type`` is registered (the routing-mode is fully
        configured for this alert path).  False during partial
        onboarding when only owner_admin (or nothing) is registered.

    The resolver uses ``primary_present`` to decide whether to short-
    circuit on this path or fall through to legacy: aggregate-only
    results are NOT enough — the primary must exist or the operator
    sees CRITICAL routed differently from non-CRITICAL.
    """
    key = persona_mapping.canonical_route_key(alert_type)
    targets: list[AlertTarget] = []
    primary_present = False

    # ── Permission-driven fan-out (the matrix is the SSOT) ──────────
    # The alert posts to EVERY staff role whose effective per-account
    # permissions include this alert type's feature, whose per-role
    # toggle is on, and whose group is bound.  Granting Fleet the
    # Safety-Events feature in the Permissions matrix therefore routes
    # events to Fleet's group too — no static map to keep in sync.
    tenant = None
    try:
        from infra.services import get_tenant_db
        tenant = await get_tenant_db(account_id)
    except Exception as e:
        logger.debug("tenant handle unavailable acct=%d: %s", account_id, e)

    async def _route_on(role: str) -> bool:
        """Per-role toggle; also honors the legacy account-wide
        ``persona_route.{key}`` written before toggles were per-role."""
        if tenant is None:
            return True  # fail-open — a settings blip never silences alerts
        try:
            per_role = await tenant.get_account_setting(
                account_id, f"persona_route.{role}.{key}", default="",
            )
            if per_role:
                return per_role != "0"
            legacy = await tenant.get_account_setting(
                account_id, f"persona_route.{key}", default="1",
            )
            return legacy != "0"
        except Exception:
            return True

    roles_for_type: list[str] = []
    try:
        for role in persona_mapping.STAFF_ROLES:
            if key in await persona_mapping.types_for_role(account_id, role):
                roles_for_type.append(role)
    except Exception as e:
        logger.debug("types_for_role failed acct=%d type=%s: %s",
                     account_id, alert_type, e)
    if not roles_for_type:
        # Permission lookup unavailable → the static default map keeps
        # alerts flowing (fail-open to the type's home role).
        home = persona_mapping.persona_for_alert(alert_type)
        if home != persona_mapping.OWNER_ADMIN:
            roles_for_type = [home]

    seen_chats: set[int] = set()
    for role in roles_for_type:
        if not await _route_on(role):
            continue
        grp = await db.get_persona_group(account_id, role)
        if grp is None:
            continue
        primary_present = True
        if grp.chat_id in seen_chats:
            continue  # two roles sharing one chat (small fleets)
        seen_chats.add(grp.chat_id)
        targets.append(AlertTarget(
            chat_id=grp.chat_id,
            message_thread_id=None,
            is_aggregate=False,
            persona=role,
        ))

    # owner_admin-homed types (system/reescalate) post to the owners
    # group as their PRIMARY destination.
    if persona_mapping.persona_for_alert(alert_type) == persona_mapping.OWNER_ADMIN:
        owners = await db.get_persona_group(account_id, persona_mapping.OWNER_ADMIN)
        if owners is not None:
            primary_present = True
            if owners.chat_id not in seen_chats:
                seen_chats.add(owners.chat_id)
                targets.append(AlertTarget(
                    chat_id=owners.chat_id,
                    message_thread_id=None,
                    is_aggregate=False,
                    persona=persona_mapping.OWNER_ADMIN,
                ))
    # Cross-post to the owner_admin aggregate ONLY for CRITICAL.
    elif sev == CRITICAL_SEVERITY:
        aggregate = await db.get_persona_group(account_id, persona_mapping.OWNER_ADMIN)
        if aggregate is not None and aggregate.chat_id not in seen_chats:
            targets.append(AlertTarget(
                chat_id=aggregate.chat_id,
                message_thread_id=None,
                is_aggregate=True,
                persona=persona_mapping.OWNER_ADMIN,
            ))

    return targets, primary_present


# Same pipeline-key map ``pipeline.py`` uses — duplicated here so the
# resolver stays a leaf dependency of the alerting package and tests
# can exercise both modes without importing the full pipeline module.
# Drift between this map and pipeline._PIPELINE_TO_ROUTE_KEY would
# silently route legacy-mode alerts to the wrong topic, so a
# parity test in test_routing_resolver.py asserts they match.
_PIPELINE_TO_ROUTE_KEY: dict[str, str] = {
    "fault":        "faults",
    "faults":       "faults",
    "health":       "health",
    "fuel":         "fuel",
    "events":       "events",
    "event":        "events",
    "camera":       "camera",
    "parking":      "parking",
    "geofence":     "geofence",
    "scorecard":    "scorecard",
    "maintenance":  "maintenance",
    "documents":    "documents",
    "doc_expiry":   "documents",
    "system":       "system",
    "samsara_sync": "system",
    "reescalate":   "system",
}


async def _resolve_single_group(
    db, account_id: int, alert_type: str,
) -> list[AlertTarget]:
    """Legacy ``alert_routing`` lookup — returns at most one target."""
    route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
    if route_key is None:
        return []
    route = await db.get_alert_route(account_id, route_key)
    if route is None:
        return []
    return [AlertTarget(
        chat_id=route.chat_id,
        message_thread_id=route.message_thread_id,
        is_aggregate=False,
        persona="",
    )]
