"""Per-account lifecycle policy — which source may ADD or AUTO-INACTIVATE rows.

The sibling of ``precedence``: precedence answers "whose VALUE wins a
field", this answers the same question about the ROW — who may create
one, and whose silence-sweep may retire one.  Same storage
(``account_settings``), same fail-open posture, same registry of
sources.

Two decisions shape it:

* **Defaults are today's unconditional behaviour.**  Every verb a
  source has is ON until an owner turns it off — a default that blocked
  Datatruck adds would silently strand trailers on existing accounts,
  since those exist precisely because Samsara does not carry them.

* **Only verbs that exist are offered.**  Datatruck has no inactivate
  mechanism, so a Datatruck-inactivate switch would be a stored lie —
  rendered, togglable, doing nothing.  ``LIFECYCLE_VERBS`` states which
  code paths actually exist per source, and both the stored policy and
  the config payload are clamped to it.  (An "add order" — Samsara 1,
  Datatruck 2 — is deliberately NOT here: syncs run independently, so
  an ordering between them is unenforceable; the real cross-provider
  dedup is the identity matching, and these booleans.)

``manual`` is absent on purpose: an operator adding or retiring a truck
by hand is not auto-pilot, and a policy that could switch a PERSON'S
buttons off belongs to permissions, not here.

``add`` governs CREATION only.  A source denied ``add`` still matches,
enriches and revives existing rows — blocking enrichment would freeze
VINs and odometers on trucks the account already owns, which nobody
asking "stop auto-adding vehicles" means.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The verbs each source's code paths actually implement, per entity.
#: This is a statement about CODE, so it lives in code: samsara's ingest
#: creates rows and its silence-sweep retires them; datatruck's
#: projection creates rows and retires nothing.
LIFECYCLE_VERBS: dict[str, dict[str, tuple[str, ...]]] = {
    "vehicle": {
        "samsara": ("add", "inactivate"),
        "datatruck": ("add",),
    },
}


def _setting_key(entity_type: str) -> str:
    return f"source_lifecycle:{entity_type}"


def _verbs(entity_type: str) -> dict[str, tuple[str, ...]]:
    return LIFECYCLE_VERBS.get(entity_type, {})


async def get_lifecycle_policy(
    db: Any, account_id: int, entity_type: str,
) -> dict[str, dict[str, bool]]:
    """``{source: {verb: allowed}}``, defaults merged, clamped to verbs
    that exist.  Unknown/invalid stored keys fall back to allowed."""
    verbs = _verbs(entity_type)
    policy = {s: {v: True for v in vs} for s, vs in verbs.items()}
    try:
        raw = await db.get_account_setting(
            account_id, _setting_key(entity_type), "")
    except Exception:
        # Fail-open: one tick on defaults beats a fleet frozen by a
        # transient settings read.
        logger.warning("lifecycle policy read failed acct=%s — defaults",
                       account_id, exc_info=True)
        return policy
    if raw:
        try:
            stored = json.loads(raw)
        except (TypeError, ValueError):
            stored = None
        if isinstance(stored, dict):
            for s, flags in stored.items():
                if s not in policy or not isinstance(flags, dict):
                    continue
                for v, allowed in flags.items():
                    if v in policy[s]:
                        policy[s][v] = bool(allowed)
    return policy


async def set_lifecycle_policy(
    db: Any, account_id: int, entity_type: str,
    policy: dict[str, dict[str, Any]],
) -> dict[str, dict[str, bool]]:
    """Persist, clamped to declared sources and existing verbs — a
    stored flag for a verb no code implements would be a lie that
    renders as a working switch."""
    verbs = _verbs(entity_type)
    clean: dict[str, dict[str, bool]] = {}
    for s, flags in (policy or {}).items():
        if s not in verbs or not isinstance(flags, dict):
            continue
        kept = {v: bool(flags[v]) for v in flags if v in verbs[s]}
        if kept:
            clean[s] = kept
    await db.set_account_setting(
        account_id, _setting_key(entity_type), json.dumps(clean))
    return await get_lifecycle_policy(db, account_id, entity_type)


async def may_add(db: Any, account_id: int, entity_type: str, source: str) -> bool:
    """May ``source`` CREATE a net-new row?  Unknown sources may — the
    policy only governs the mechanisms it declares."""
    policy = await get_lifecycle_policy(db, account_id, entity_type)
    return policy.get(source, {}).get("add", True)


async def may_auto_inactivate(
    db: Any, account_id: int, entity_type: str, source: str,
) -> bool:
    """May ``source``'s silence-sweep retire rows?"""
    policy = await get_lifecycle_policy(db, account_id, entity_type)
    return policy.get(source, {}).get("inactivate", True)
