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

# apply_resolution(db, account_id, entity_id, field, value) -> awaitable[None]
# Writes the chosen value onto the entity AND pins it (so no sync undoes it).
ApplyResolution = Callable[[Any, int, int, str, Any], Awaitable[None]]


@dataclass(frozen=True)
class ReconciledEntity:
    entity_type: str
    fields: tuple[str, ...]
    default_precedence: dict[str, tuple[str, ...]]
    field_labels: dict[str, str]
    sources: tuple[str, ...]
    apply_resolution: ApplyResolution


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
    """Declare a reconcilable entity.  Idempotent (re-registering replaces)."""
    _REGISTRY[entity_type] = ReconciledEntity(
        entity_type=entity_type,
        fields=tuple(fields),
        default_precedence={k: tuple(v) for k, v in default_precedence.items()},
        field_labels=dict(field_labels),
        sources=tuple(sources),
        apply_resolution=apply_resolution,
    )


def get_entity(entity_type: str) -> "ReconciledEntity | None":
    return _REGISTRY.get(entity_type)
