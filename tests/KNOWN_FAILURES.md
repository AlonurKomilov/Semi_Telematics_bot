# Known baseline test failures — stop re-deriving these

Living ledger. If a run shows ONLY these, your change is clean.
Fix one → delete its row in the same commit.

| suite | count | cause | owner |
|---|---|---|---|
| test_object_storage_reference_registry | 2 | `vehicle_documents` writes to object storage but declares no reference column in `capabilities/object_storage/references.py` — the orphan purge would read every uploaded document as unreferenced | co-dev (vehicle Documents card) |

Verified 2026-08-28 against the full suite: 3582 passed, 15 skipped, and
only the row above red.

## Cleared on 2026-08-28

The previous two rows — `test_activity_trail` (1) and
`test_vehicles_registry` route trio (3), both attributed to "fixture
interplay … platform DB not initialised" — no longer reproduce: those
suites are 86 passed. They were process-global leaks of the kind the
isolation contract now prevents (see [CLAUDE.md](CLAUDE.md)), not
fixture interplay.

Worth keeping as a caution: both rows had been "verified pre-existing
against clean baseline worktrees, twice, independently." That check was
honest and still described a symptom rather than a cause, because a
baseline worktree reproduces a leak that lives in the CODE just as
faithfully as the branch does. Confirming a failure is not new is not
the same as knowing what it is.
