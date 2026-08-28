"""Per-feature registration of reconcilable entities.

Each feature that has multi-source data declares its shape here — the fields
that can conflict, the default source precedence, the labels for the UI, the
sources to choose from, and how to APPLY a resolution (write + pin) onto the
entity.  The generic engine + precedence + conflict UI then cover it with no
bespoke code.  This is the "by feature/component" hook: adding a domain =
one ``register_reconciled_entity`` call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .engine import MANUAL_SOURCE

# apply_resolution(db, account_id, entity_id, field, value) -> awaitable[None]
# Writes the chosen value onto the entity AND pins it (so no sync undoes it).
ApplyResolution = Callable[[Any, int, int, str, Any], Awaitable[None]]


@dataclass(frozen=True)
class ReconciledEntity:
    entity_type: str
    fields: tuple[str, ...]
    default_precedence: dict[str, tuple[str, ...]]
    field_labels: dict[str, str]
    #: Every source that can OWN a value on this entity — providers plus
    #: ``manual``, which the registry injects so no declaration can
    #: forget the one source that always wins.  The DECLARED model.
    sources: tuple[str, ...]
    apply_resolution: ApplyResolution

    @property
    def provider_sources(self) -> tuple[str, ...]:
        """The CONFIGURABLE subset — what precedence may order and the
        UI may offer.  ``manual`` is deliberately absent: its rank is a
        code invariant (an operator edit always wins), and a source an
        owner could drag below a provider would let a nightly sync
        silently revert hand corrections — which would also break the
        conflicts UI, whose resolutions work by pinning as manual."""
        return tuple(s for s in self.sources if s != MANUAL_SOURCE)


# test-safe: reconciled entities are declared at import by their owning feature.
_REGISTRY: dict[str, ReconciledEntity] = {}


def register_reconciled_entity(
    entity_type: str,
    *,
    fields,
    default_precedence: dict,
    field_labels: dict,
    sources,
    apply_resolution: ApplyResolution,
) -> None:
    """Declare a reconcilable entity.  Idempotent (re-registering replaces).

    ``manual`` is injected into ``sources`` HERE, structurally, rather
    than expected from the three call sites that could each forget it:
    an operator's edit is a first-class source on every entity — the
    highest-ranked one — and for as long as it was only a hardcoded
    ``-1`` inside the engine, the model claimed the sources were
    "datatruck, samsara" while the behaviour said otherwise.  Callers
    keep declaring providers only.
    """
    declared = tuple(sources)
    if MANUAL_SOURCE not in declared:
        declared = declared + (MANUAL_SOURCE,)
    _REGISTRY[entity_type] = ReconciledEntity(
        entity_type=entity_type,
        fields=tuple(fields),
        default_precedence={k: tuple(v) for k, v in default_precedence.items()},
        field_labels=dict(field_labels),
        sources=declared,
        apply_resolution=apply_resolution,
    )


def get_entity(entity_type: str) -> "ReconciledEntity | None":
    return _REGISTRY.get(entity_type)
