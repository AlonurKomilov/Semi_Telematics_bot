"""DEPRECATED shim — moved to ``capabilities.source``.

The package arbitrates every SOURCE a record's values can come from —
Samsara, Datatruck, and the customer's own operators (``manual``, which
always wins) — for three features (vehicles, drivers, loads).  A home
under ``integrations/`` described the TRIGGER (only integration writes
invoke the merge) but mis-described the concept, and the owner read
"integrations-only" off the path twice: a name that misleads the person
who owns the codebase will mislead everyone after them.  The whole
package moved atomically — never split: ``merge_fields`` does fill,
precedence and manual-pin in one loop, and tearing that across two
homes is this repo's documented incident pattern.

Kept as a re-export for one release for the same reason the
``settings_registry`` shim was: a branch written before the move must
not fail to import mid-merge.  It re-exports the SAME objects — there
is no second engine — so a caller on the old path and one on the new
resolve identical rules.

Import from ``capabilities.source`` in new code.  When no import of
this path remains, delete the directory.
"""

from capabilities.source import (  # noqa: F401
    MANUAL_SOURCE,
    MergeResult,
    clear_conflict,
    count_open,
    get_entity,
    get_precedence,
    is_unset,
    list_open,
    merge_fields,
    precedence_options,
    record_conflict,
    register_reconciled_entity,
    resolve,
    set_precedence,
    source_rank,
    sync_batch,
)
