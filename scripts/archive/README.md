# Archived scripts

Tools whose job is done.  They are records of a migration, not
runnable procedures: they reason about fields that no longer exist.

- `fold_pair_width.py`, `strip_stale_crumbs.py` — the verb/scope
  migration's pair-death pre-flight (2026-09-02).  The owner's dry-run
  found ten stale recruiter rows, the sweep removed them, the fold
  reported zero rows, and the physical flip followed.  Both imported
  `classify_pairs` / `stale_narrow_crumbs` from
  `capabilities/permissions/fold.py`, which the flip retired.
- `preflight_person_own_fold.py`, `strip_stale_own_crumbs.py` — the
  person fold's pre-flight (2026-09-04).  The owner's report found 53
  rows: nine fleet rows holding the risk-summary own flag the seed still
  granted (a live default, decided ON), and forty-four recruiter rows
  holding the four own flags 327bf160 had turned off in the seed — the
  other half of the residue the pair sweep removed.  The sweep took
  them, and the fold followed.  Both imported `plan_own_preflight` /
  `stale_own_crumbs` from `capabilities/permissions/fold.py`, which
  the fold retired.
