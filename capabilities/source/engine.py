"""Generic cross-source field merge — the mechanism behind source precedence
and conflict detection, decoupled from any feature.

A feature registers its reconcilable shape (see ``registry.py``) and its
integration write path calls ``merge_fields`` with the current row's values +
provenance and the incoming source's values.  Nothing here knows about
vehicles — the same engine serves drivers, health, or any future domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MANUAL_SOURCE = "manual"


def is_unset(v: Any) -> bool:
    """A value counts as 'not provided' — so it never overwrites."""
    return v is None or v == "" or v == 0


def pin_manual(provenance: dict | None, fields) -> dict:
    """Mark ``fields`` as operator-owned on a provenance map.

    THE local write path's entry into this package.  A hand edit never
    runs ``merge_fields`` — it writes its values directly and records
    that an operator now owns those fields, which is what makes every
    later sync yield to them (``source_rank`` puts manual above any
    configured order).  Three mixins previously inlined this loop; one
    helper means the pin semantics cannot drift per feature.

    Returns a NEW dict — callers hold frozen rows and json blobs, and
    mutating a shared provenance map in place is how a pin would leak
    between two records.
    """
    prov = dict(provenance or {})
    for f in fields:
        prov[f] = MANUAL_SOURCE
    return prov


def source_rank(source: str, order: tuple[str, ...]) -> int:
    """Lower = higher priority.  ``manual`` (an operator edit) always wins; a
    source not named in the order is lowest (fills a gap, can't outrank)."""
    if source == MANUAL_SOURCE:
        return -1
    try:
        return list(order).index(source)
    except ValueError:
        return len(order) + 1


@dataclass
class MergeResult:
    updates: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)


def merge_fields(
    *,
    current: dict[str, Any],
    provenance: dict[str, str],
    owner_fallback: str | None,
    incoming: dict[str, Any],
    source: str,
    fields: tuple[str, ...],
    precedence: dict[str, tuple[str, ...]],
) -> MergeResult:
    """Decide which fields an integration write may change, advance the
    provenance map, and surface genuine cross-source conflicts.

    Rules per field: blank-skip → fill-empty → agree (clear) → manual-pin →
    precedence (the configured owner wins; a manual pin beats all).  A missing
    provenance entry falls back to ``owner_fallback`` (the row's existing
    source), so rows that predate provenance behave correctly.

    ``current`` is ``{field: stored_value}``; ``incoming`` is the normalized
    integration row.  Returns updates / new provenance / conflicts / the
    fields that now AGREE (so a prior open conflict can be cleared).
    """
    prov: dict[str, str] = dict(provenance or {})
    res = MergeResult(provenance=prov)
    for f in fields:
        inc = incoming.get(f)
        if is_unset(inc):
            continue
        cur = current.get(f)
        if is_unset(cur):
            res.updates[f] = inc
            prov[f] = source
            continue
        if str(inc) == str(cur):
            res.cleared.append(f)            # sources agree → resolve any conflict
            continue
        owner = prov.get(f) or owner_fallback
        if owner == MANUAL_SOURCE:
            continue                         # operator pin — not a source conflict
        order = precedence.get(f, ())
        if source_rank(source, order) <= source_rank(owner or "", order):
            res.updates[f] = inc
            prov[f] = source
            win_val, win_src, lose_val, lose_src = inc, source, cur, owner
        else:
            win_val, win_src, lose_val, lose_src = cur, owner, inc, source
        # A genuine disagreement between two DIFFERENT integration sources.
        if owner and owner != source:
            res.conflicts.append({
                "field": f,
                "current_value": win_val, "current_source": win_src,
                "incoming_value": lose_val, "incoming_source": lose_src,
            })
    return res
