# Archived scripts

Tools whose job is done.  They are records of a migration, not
runnable procedures: they reason about fields that no longer exist.

- `fold_pair_width.py`, `strip_stale_crumbs.py` — the verb/scope
  migration's pair-death pre-flight (2026-09-02).  The owner's dry-run
  found ten stale recruiter rows, the sweep removed them, the fold
  reported zero rows, and the physical flip followed.  Both imported
  `classify_pairs` / `stale_narrow_crumbs` from
  `capabilities/permissions/fold.py`, which the flip retired.
