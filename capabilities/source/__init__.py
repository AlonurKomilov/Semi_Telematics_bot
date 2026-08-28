"""Source — who supplied a record's values, and whose value wins.

The problem it owns: when several SOURCES describe the same canonical
record — Samsara, Datatruck, or the customer's own operators — who wins
per field, and how is a genuine disagreement surfaced?  ``manual`` (an
operator edit) is a first-class source and ALWAYS wins: the conflict-
resolution UI works by pinning a chosen value as manual so no sync can
undo it, which is why that rank lives in code, never in config.

Named for the CONCEPT, not the trigger.  Only integration write paths
invoke ``merge_fields`` today — a local edit writes directly and pins
its provenance — but the thing being arbitrated is sources, of which
integrations are one kind.  This package lived under
``capabilities/integrations/reconciliation/`` until 2026-08-28; it
moved WHOLE, and must never be split: ``merge_fields`` does fill,
precedence and manual-pin in one loop, and tearing one function across
two homes is this repo's documented incident pattern.  "Reconcile"
survives as the mechanism's name inside.

Structure:
  * ``engine``     — pure ``merge_fields`` (blank-skip → fill → agree → pin →
                     precedence); knows nothing about any feature.
  * ``registry``   — features declare their reconcilable shape via
                     ``register_reconciled_entity`` (the "by feature" hook).
  * ``precedence`` — per-account, per-entity source priority (account_settings).
  * ``conflicts``  — the generic ``data_conflicts`` store + resolution dispatch.

A feature = one ``register_reconciled_entity(...)`` call + a call to
``merge_fields`` in its integration write path.  Adding a domain (drivers,
health, …) needs no new engine code.
"""

from .conflicts import (  # noqa: F401
    clear_conflict,
    count_open,
    list_open,
    record_conflict,
    resolve,
    sync_batch,
)
from .engine import (  # noqa: F401
    MANUAL_SOURCE,
    MergeResult,
    is_unset,
    merge_fields,
    pin_manual,
    source_rank,
)
from .precedence import (  # noqa: F401
    get_precedence,
    precedence_options,
    set_precedence,
)
from .registry import (  # noqa: F401
    ReconciledEntity,
    get_entity,
    register_reconciled_entity,
)
