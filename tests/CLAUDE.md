# The test suite — how it is built, and what it will not let you do

The LAW (where a test lives, the four layout rules) is in the root
[CLAUDE.md](../CLAUDE.md) §"Tests live with the code they test". This
file is the machinery: what the fixtures actually do, what isolation you
get for free, and the traps that have already cost someone a day.

Shape: every layer owns its tests — around 48 packages hold roughly 240
files, some 55 stay here, ~3,580 tests in total. Those numbers drift
daily (three people write to this tree); the SHAPE is the stable part,
and `test_test_layout.py` is what actually enforces it.

## Where does my test go?

| The test is about… | It lives in |
|---|---|
| one package's behaviour | `<pkg>/tests/` — `features/kpi/tests/`, `adapters/storage/tests/` |
| a rule that binds the WHOLE repo | `tests/` (here) |
| two packages, where one OWNS the rule | with the owner, not the consumer |
| two packages that genuinely co-own it | `tests/` (here) |

The third row is the one people get wrong. `test_staleness_contract.py`
imports `integrations.shared.history_backfill`, and still belongs to
`data_lifecycle`: the flooring rule is DEFINED by `timegrid`, and the
backfill writer is the consumer the test checks has not forked it.
Filing a guard under the consumer puts it where the rule is not.

If a file is genuinely two things, it is two files —
`test_source_ts.py` was split 6/9 for exactly this reason.

## The database: one template, copied per test

`pg_db` gives every test **its own database**, made with `CREATE
DATABASE … TEMPLATE …`, which Postgres implements as a file copy. The
schema is migrated ONCE per worker into the template; a test then pays
the copy, not the 194 migrations. That is the difference between 34.5s
and ~2.6s per DB test.

Two consequences you must hold in your head:

1. **You cannot leak state through the database.** If test B sees test
   A's row, the cause is somewhere else — look at process globals.
2. **Account ids repeat.** A fresh copy restarts the sequence, so
   nearly every test's first account is the same id. Anything cached in
   the process under an account id is therefore a *valid-looking hit*
   for the next test. This is not theoretical; see below.

Each xdist worker gets its own database on the shared server
(`_per_worker_database`, suffixed with `PYTEST_XDIST_WORKER`).

## Isolation you get for free

`conftest.py`'s `_isolate_process_caches` is autouse, so every test
starts and ends with:

- **every cache in `_PROCESS_CACHES` cleared** — anything keyed by
  account / user / model id (~35 of them today).
- **every registry in `_GLOBAL_REGISTRIES` snapshot/restored** — only
  three qualify, and the list is short on purpose (see the warning
  below).

Nothing is imported to do this: the fixture looks modules up in
`sys.modules`, so a test that never touches permissions pays nothing.

**Adding a module-level dict/list/set to production code?**
`tests/test_global_state_isolation.py` will fail until you decide which
of three it is:

- **cleared** — add it to `_PROCESS_CACHES`. Right for any cache keyed
  by something that repeats between tests.
- **snapshot/restored** — add it to `_GLOBAL_REGISTRIES`. Right ONLY if
  every entry is registered *before any test runs*.
- **annotated** — `# test-safe: <why it cannot cross a test boundary>`
  on the definition. A bare marker is rejected; the reason is the point.

⚠️ **Never restore a lazily-populated registry.** `_DATASETS` is filled
by `make_discover(_CONTRIBUTORS)`, which imports contributors on FIRST
CALL. Snapshot/restoring it meant the snapshot was empty, teardown wiped
what discovery had just registered, and four tests read an empty
registry. The question that decides this is *"when does production fill
it?"* — **not** *"do tests mutate it?"*. That wrong question is what put
`_DATASETS` in the restore list for a run.

## Parallelism, and what it does not promise

`addopts` pins `-n auto --dist loadfile`. Every test in a FILE runs on
one worker in collection order, so an **intra-file** leak fails the same
way twice and can be chased.

Which FILES share a worker still varies run to run. A cross-file leak is
therefore still a coin flip — that is what the isolation contract above
exists to prevent, and why "it passed on re-run" is never evidence of a
fix. Two consecutive green runs are.

## The guards here, and what each one catches

| File | Catches |
|---|---|
| `test_test_layout.py` | a tests dir outside `testpaths` (invisible: runs nothing, skips nothing, stays green), a missing `__init__.py` (same-named modules collide), a loose `test_*.py` beside source, and package tests shipping in the Docker image |
| `test_global_state_isolation.py` | a process-global nobody has decided about |
| `test_layer_boundaries.py` | cross-layer imports, raw `vehicles` reads with no stance on retired rows |
| `test_undefined_names.py` | pyflakes F821 across the backend |
| `test_object_storage_layout.py` | the tenant file tree, and that exactly one `conftest.py` exists |

## Rules that look like style and are not

**Never compute the repo root from `__file__`.** Import `REPO` from
[`_repo.py`](_repo.py) — it walks up to the `pytest.ini` sentinel, so it
survives a file changing depth. A hand-rolled `Path(__file__).parents[3]`
was resolving to `/home/abcdev` and auditing a home directory, passing
vacuously the whole time.

**A guard you have never seen fail proves nothing.** Every rule here was
mutation-tested: remove the thing it protects, watch it go red, put it
back. Two of today's guards had bugs that only surfaced that way.

**Prune test paths in any guard that scans source.** Use
`is_test_path()` from `_repo.py` (it compares path PARTS, so `contests/`
is not a test). Guards written before the migration assumed tests lived
outside `features/`/`capabilities/`; moving them enrolled 274 test files
as production source, and one guard started enforcing production rules
on test code.

## Before you say "it's flaky"

It usually is not. Today's three "flaky" failures were: a cache keyed by
an account id that repeats, a second cache of the same family that was
half-isolated, and a `FakeChannel` registered with no teardown that
failed a test in a *different package*. Each was reproducible once you
knew what to hold still.

Check in this order: run it alone → run its file → run its file with the
suspected neighbour → check `KNOWN_FAILURES.md`.
