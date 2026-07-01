"""Reconciliation — the shared merge / precedence / conflict engine for
multi-integration data.

The problem it owns: when two integrations write the SAME field of the SAME
canonical record, who wins, and how is a genuine disagreement surfaced?  It's
the middle layer of the integration pipeline — provider adapters normalize
their resources, this reconciles them into the canonical feature model,
detecting conflicts as it goes.

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
