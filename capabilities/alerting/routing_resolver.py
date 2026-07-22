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
    subtype: str = "",
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
                db, account_id, alert_type, sev, subtype,
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

        return await _resolve_single_group(db, account_id, alert_type, subtype)
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


async def _subtype_selected(
    tenant, account_id: int, settings_key: str, subtype: str, route_key: str,
) -> bool:
    """Sub-category gate, fully fail-open.

    Returns True (deliver) unless the account has an explicit
    sub-category selection for ``route_key`` that EXCLUDES ``subtype``.
    Empty/missing selection = ALL.  No subtype, no tenant handle,
    vocab-less type, unknown subtype, or a settings read failure all
    deliver — filtering must never eat an alert it wasn't told about.

    ``settings_key`` is the full account_setting key so each caller
    supplies its own namespace: ``persona_subtypes.{role}.{key}`` for
    role mode, ``forum_subtypes.{key}`` for single mode.
    """
    if not subtype or tenant is None:
        return True
    vocab = persona_mapping.ALERT_SUBTYPES.get(route_key)
    if not vocab or subtype not in vocab:
        return True
    try:
        sel = await tenant.get_account_setting(
            account_id, settings_key, default="",
        )
    except Exception:
        return True
    if not sel:
        return True
    return subtype in sel.split(",")


def _match_topics(topics, route_key: str, subtype: str) -> list:
    """Custom topics matching this alert (pure — no I/O).

    A topic matches when its ``alert_type`` equals ``route_key`` and,
    if it narrows by sub-category, ``subtype`` is one of its kinds.  A
    subtype-narrowed topic never takes a subtype-less alert.
    """
    out = []
    for tp in topics:
        if tp.alert_type != route_key:
            continue
        if tp.subtypes:
            if not subtype or subtype not in tp.subtypes.split(","):
                continue
        out.append(tp)
    return out


async def _resolve_per_persona(
    db, account_id: int, alert_type: str, sev: str, subtype: str = "",
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

    seen_dests: set[tuple[int, int | None]] = set()
    for role in roles_for_type:
        grp = await db.get_persona_group(account_id, role)
        if grp is None:
            continue
        # A bound role means the mode IS configured for this type —
        # even when its toggle or sub-category selection then declines
        # the post.  Otherwise a declined subtype would fall back to
        # the LEGACY forum and resurrect alerts the manager filtered
        # out on purpose.
        primary_present = True
        if not await _route_on(role):
            continue
        if not await _subtype_selected(
            tenant, account_id, f"persona_subtypes.{role}.{key}", subtype, key,
        ):
            continue
        # ── Custom topics: a user-defined rule (name + type +
        # sub-category narrowing + optional forum thread) REPLACES the
        # role's default flat post when it matches this alert.  A
        # subtype-narrowed topic only takes subtype-known alerts; an
        # alert with no matching custom topic falls to the default.
        matched_topics = []
        try:
            matched_topics = _match_topics(
                await db.list_alert_topics(account_id, role), key, subtype,
            )
        except Exception as e:
            logger.debug("alert_topics lookup failed acct=%d role=%s: %s",
                         account_id, role, e)

        if matched_topics:
            for tp in matched_topics:
                dest = (grp.chat_id, tp.thread_id)
                if dest in seen_dests:
                    continue
                seen_dests.add(dest)
                targets.append(AlertTarget(
                    chat_id=grp.chat_id,
                    message_thread_id=tp.thread_id,
                    is_aggregate=False,
                    persona=role,
                ))
            continue

        if (grp.chat_id, None) in seen_dests:
            continue  # two roles sharing one chat (small fleets)
        seen_dests.add((grp.chat_id, None))
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
            if (owners.chat_id, None) not in seen_dests:
                seen_dests.add((owners.chat_id, None))
                targets.append(AlertTarget(
                    chat_id=owners.chat_id,
                    message_thread_id=None,
                    is_aggregate=False,
                    persona=persona_mapping.OWNER_ADMIN,
                ))
    # Cross-post to the owner_admin aggregate ONLY for CRITICAL.
    elif sev == CRITICAL_SEVERITY:
        aggregate = await db.get_persona_group(account_id, persona_mapping.OWNER_ADMIN)
        if aggregate is not None and (aggregate.chat_id, None) not in seen_dests:
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
    db, account_id: int, alert_type: str, subtype: str = "",
) -> list[AlertTarget]:
    """``alert_routing`` lookup — one default target, at parity with the
    per-persona path's two advanced features:

      • an account-wide sub-category selection (``forum_subtypes.{key}``)
        gates the post (declined subtype → [] → DM fallback, exactly
        what role mode does);
      • a matching custom topic (``alert_topics`` under the
        ``FORUM_SENTINEL`` persona) REPLACES the default forum post,
        routing the alert to its own thread instead.
    """
    route_key = _PIPELINE_TO_ROUTE_KEY.get(alert_type)
    if route_key is None:
        return []
    route = await db.get_alert_route(account_id, route_key)
    if route is None:
        return []

    tenant = None
    try:
        from infra.services import get_tenant_db
        tenant = await get_tenant_db(account_id)
    except Exception as e:
        logger.debug("tenant handle unavailable acct=%d: %s", account_id, e)

    # Sub-category gate (gates the default AND custom topics — same
    # gate-before-topics ordering as the per-persona path).
    if not await _subtype_selected(
        tenant, account_id, f"forum_subtypes.{route_key}", subtype, route_key,
    ):
        return []

    # Custom topics REPLACE the default forum post when one matches.
    matched_topics = []
    try:
        matched_topics = _match_topics(
            await db.list_alert_topics(account_id, persona_mapping.FORUM_SENTINEL),
            route_key, subtype,
        )
    except Exception as e:
        logger.debug("alert_topics lookup failed acct=%d (forum): %s",
                     account_id, e)

    if matched_topics:
        seen: set[tuple[int, int | None]] = set()
        out: list[AlertTarget] = []
        for tp in matched_topics:
            dest = (route.chat_id, tp.thread_id)
            if dest in seen:
                continue
            seen.add(dest)
            out.append(AlertTarget(
                chat_id=route.chat_id,
                message_thread_id=tp.thread_id,
                is_aggregate=False,
                persona="",
            ))
        return out

    return [AlertTarget(
        chat_id=route.chat_id,
        message_thread_id=route.message_thread_id,
        is_aggregate=False,
        persona="",
    )]
