# Known baseline test failures — stop re-deriving these

Living ledger. If a run shows ONLY these, your change is clean.
Fix one → delete its row in the same commit.

*(empty — the suite is green)*

| suite | count | cause | owner |
|---|---|---|---|

A row here is a debt, not a status. If you are about to add one, first
ask whether the failure is actually understood: two of the three rows
this file has carried turned out to be process-global leaks rather than
the "fixture interplay" they were filed as, and one was a test that had
EXPIRED rather than broken.

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

The `test_object_storage_reference_registry` pair (vehicle_documents
declaring no reference column) was fixed in b7e7df97, and
`test_in_progress_trip_duration_is_sane` in 7f4c3b33 — it had not broken,
it had expired: a fixed trip start measured against `now` crossed its own
400-day bound on 2026-08-30.
