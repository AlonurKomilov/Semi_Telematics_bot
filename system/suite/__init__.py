"""The test board — what the suite did, and who was holding it.

An operator-only service, like everything under ``system/``: reachable
through ``/system/suite/*`` behind ``require_system_owner``, named by no
``can_*`` flag, invisible to Team Management.

It does not run tests. pytest runs them, wherever a person or CI runs
it; a reporter POSTs the summary afterwards and this shows it. The
distinction matters: the runner is the repo's, and the board is only its
mirror — the half GitHub Actions shows, for the runs Actions never sees,
which on this platform is most of them.

Its one real question is attribution. "The suite is red" was never the
useful sentence here — three sessions and a person write to one tree, so
the useful sentence is "this went red between these two commits". Every
run is kept, so a failing test can be looked back to the last run that
passed it; the storage that computes the bracket lives in
``adapters/storage/suite_runs.py``.
"""
