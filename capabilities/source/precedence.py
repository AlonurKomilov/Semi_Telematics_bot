"""Per-account, per-entity source precedence — stored in ``account_settings``.

Generic on ``entity_type``: reads the entity's registered default and merges
any per-account override on top.  Functions take the tenant ``db`` (the
Database), so nothing new registers on the storage mixin.
"""

from __future__ import annotations

import json
from typing import Any

from .engine import MANUAL_SOURCE
from .registry import get_entity


def _setting_key(entity_type: str) -> str:
    # Back-compat: vehicle keeps its original key so a config an owner already
    # set survives the extraction.  New entities are namespaced.
    if entity_type == "vehicle":
        return "vehicle_field_precedence"
    return f"field_precedence:{entity_type}"


async def get_precedence(
    db: Any, account_id: int, entity_type: str,
) -> dict[str, tuple[str, ...]]:
    """The entity's per-account precedence, merged over its registered default;
    absent / invalid override keys fall back to the default."""
    ent = get_entity(entity_type)
    if ent is None:
        return {}
    merged = {k: tuple(v) for k, v in ent.default_precedence.items()}
    try:
        raw = await db.get_account_setting(account_id, _setting_key(entity_type), "")
    except Exception:
        raw = ""
    if raw:
        try:
            override = json.loads(raw)
        except (TypeError, ValueError):
            override = None
        if isinstance(override, dict):
            for f, order in override.items():
                if f in merged and isinstance(order, list) and order:
                    # ``manual`` stripped defensively: nothing writes it
                    # any more, but a stored order carrying it would lie
                    # to the UI — the engine ranks manual -1 regardless
                    # of any order, so an entry here is dead text that
                    # LOOKS configurable.
                    cleaned = tuple(
                        str(s) for s in order if str(s) != MANUAL_SOURCE
                    )
                    if cleaned:
                        merged[f] = cleaned
    return merged


async def set_precedence(
    db: Any, account_id: int, entity_type: str, primary_by_field: dict[str, str],
) -> dict[str, list[str]]:
    """Persist a per-field PRIMARY-source choice; expands each into the full
    order ``[primary, …other sources]`` and ignores unknown fields/sources."""
    ent = get_entity(entity_type)
    if ent is None:
        return {}
    order_map: dict[str, list[str]] = {}
    # ``provider_sources``, not ``sources``: manual is declared but not
    # configurable.  Accepting it as a primary would store a no-op that
    # reads as a choice, and expanding orders over the full set would
    # write "manual" into config — the exact lie get_precedence strips.
    for f, primary in (primary_by_field or {}).items():
        if f not in ent.default_precedence or primary not in ent.provider_sources:
            continue
        order_map[f] = (
            [primary] + [s for s in ent.provider_sources if s != primary]
        )
    await db.set_account_setting(
        account_id, _setting_key(entity_type), json.dumps(order_map),
    )
    return order_map


async def precedence_options(db: Any, account_id: int, entity_type: str) -> dict:
    """UI payload: each field with its label + current PRIMARY source, plus the
    sources to choose from."""
    ent = get_entity(entity_type)
    if ent is None:
        return {"sources": [], "fields": []}
    prec = await get_precedence(db, account_id, entity_type)
    return {
        # Providers only — this list feeds pick-lists.  Manual is a
        # declared source but never an OPTION: the UI states it as a
        # fixed rule ("Manual edits — always win"), not a choice.
        "sources": list(ent.provider_sources),
        "fields": [
            {
                "key": f,
                "label": ent.field_labels.get(f, f),
                "primary": (prec.get(f) or ent.provider_sources)[0],
            }
            for f in ent.default_precedence
        ],
    }
