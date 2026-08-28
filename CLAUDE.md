# Model & Delegation Rules

## Default execution
You (the main session) handle all routine work directly: implementation,
bug fixes, tests, refactors, file edits. Do not delegate routine work.

## When to consult the fable-advisor agent
Consult `fable-advisor` (expensive senior model — use sparingly, at most
once or twice per task) ONLY when one of these is true:

1. **Architectural fork**: two or more viable designs and the choice is
   hard to reverse later (schema design, service boundaries, auth flow,
   integration strategy with Samsara/DAT/Motive).
2. **Stuck**: you have attempted a fix 2+ times and the root cause is
   still unclear.
3. **Security-sensitive**: changes touching auth, tenant isolation,
   payment (Stripe), credentials, or driver PII (FMCSA/DOT data).
4. **Destructive/irreversible**: database migrations that drop or
   transform data, changes to billing logic.
5. **Ambiguous requirements**: the task can be interpreted in
   conflicting ways and guessing wrong wastes significant work.

When consulting, give the advisor: the goal, what you tried, the exact
decision needed, and relevant file paths. Then follow its plan. Do not
re-litigate its decision unless new facts emerge.

Do NOT consult the advisor for: formatting, naming, simple bugs, adding
endpoints that follow existing patterns, writing tests, or anything a
competent mid-level engineer would decide alone.

## After completing work
Before suggesting a commit, invoke the `code-reviewer` agent on the diff.
Fix Critical issues. If the reviewer flags an architectural/security
design problem, escalate that one question to `fable-advisor`.

## Cost discipline
- Keep advisor consultations short and specific — one question, one answer.
- Never send the advisor on broad exploration; give it exact file paths.
- Prefer finishing in the current session over spawning agents for work
  you can do directly.

# Committing — the guard runs, read what it says

`scripts/githooks/pre-commit` blocks a commit that would not parse, that
carries an undefined name, or that stages a secret; it warns when the
staged set has the footprint of `git add -A`. Install once with
`git config core.hooksPath scripts/githooks`.

**Stage explicit paths — never `git add -A`, never `git commit -a`.**
Two AIs and a human share this working tree, so anything dirty that is
not yours belongs to someone mid-edit. A shared index broke `main`
twice in one week: once by committing a caller without its callee, once
by committing a mid-edit component. Both times the author only ran
`git add -A`.

`git commit -- <path>` fails on a NEW file. The recipe that works:
verify `git diff --cached --name-only` is empty, `git add -- <files>`,
confirm the staged list, then commit.

Run `./scripts/where.sh` when you finish a task: it reports what is
uncommitted (runs today, exists nowhere else), undeployed, and unpushed.
Rules and the incident behind each: [scripts/githooks/README.md](scripts/githooks/README.md)

# Naming rules

- **Role words never go into shared identifiers.** Persona words
  (`fleet`, `safety`, `dispatch`, `hr`, `accounting`…) are live role
  identifiers here (role strings, subdomains, shells); role-flavored UI
  text is GENERATED per active view. Shared/wire data — API keys,
  schema fields, type names — is named after the domain noun
  (`vehicles`, not `fleet`; Vehicle is the parent of truck and
  trailer). Persona words are correct only in genuinely per-role
  artifacts (`FleetShell`, `SafetyHero`). Full rule + the wire-key
  rename recipe (deprecated same-object alias + alias==primary test):
  [docs/architecture/PERSONA.md](docs/architecture/PERSONA.md)
  §"Naming: role words vs domain nouns".

# Tests live with the code they test

A package owns its tests in its own `tests/` subfolder —
`features/kpi/tests/`, `capabilities/alerting/tests/`,
`adapters/storage/tests/`. Not beside the source (that clutters the
package), and not in the repo-root `tests/` (that is how a test gets
orphaned from the code it guards). 45 packages follow this.

The root `tests/` keeps only what belongs to no single package:
repo-wide structural guards, and tests that cross layers.

**Four rules, enforced by `tests/test_test_layout.py`:**

1. Every package `tests/` dir sits under a `testpaths` entry in
   `pytest.ini`. A tests directory outside `testpaths` is *invisible* —
   its tests do not run, nothing is skipped, and nothing turns red. The
   suite just gets quietly smaller. This is not hypothetical: 206 of 277
   files were silently skipping while CI reported green.
2. Every package `tests/` dir has an `__init__.py`. Without it two files
   named `test_service.py` in different packages collide on module name.
3. No loose `test_*.py` beside package source — the subdirectory form is
   what the guards can see.
4. `.dockerignore` carries `**/tests/`. A bare `tests/` is anchored to
   the build-context root, so it excludes the root suite and nothing
   else — package-owned tests then ship inside the production image
   (274 files, 3MB, confirmed with a build probe).

**Adding a test dir to a package that has none:** create
`<pkg>/tests/__init__.py`, and check the package's top-level name is
already in `testpaths`. If it is not, add it — otherwise rule 1 fails
and the guard tells you so.

**Never compute the repo root from `__file__`.** Import `REPO` from
`tests/_repo.py`; it walks up to the `pytest.ini` sentinel, so it
survives a file moving between depths. Hand-rolled roots
(`Path(__file__).parents[3]`) break silently on a move — one of them was
resolving to `/home/abcdev` and auditing a home directory instead of the
repo, passing vacuously the whole time.

# Domain SSOTs — read before touching

- **Tenant file layout** (anything that calls `ObjectStorage.put`, any
  path under `data/userdata/`, the `_generic` holding pen, company
  folders): [capabilities/object_storage/LAYOUT.md](capabilities/object_storage/LAYOUT.md)
  is the law. Every tenant file lives under
  `account-{id}/{COMPANY DISPLAY NAME}/…` — **nothing but a company
  folder or `_generic` may sit at the account root**, because that tree
  is mirrored into the customer's own Drive where each top-level folder
  reads as one of their businesses. Resolve a vehicle's company by
  telematics id BEFORE unit number (unit numbers are reused across
  companies). `tests/test_object_storage_layout.py` enforces it.

- **The warehouse** (anything named `warehouse`, tiered history,
  grains live·minute·hour·day·week, the `warehouse.*` Postgres
  schema): [docs/architecture/warehouse.md](docs/architecture/warehouse.md)
  is the law — orientation, reading rules (surfaces + `source_ts`
  staleness + `registry_id` joins), and the add-a-dataset recipe.
  Physical warehouse tables are machinery-internal (CI-guarded);
  never assume 5-minute snapshots — the minute grain replaced them
  (2026-08).

# Project rituals

- **After building or modifying any user-facing feature** (dashboard page,
  public form, email, bot message), run two closing audits before calling
  the work done:
  1. **Design-system pass** — check the changed frontend files against
    [interfaces/dashboard/design.md](interfaces/dashboard/design.md) and
    [interfaces/dashboard/CLAUDE.md](interfaces/dashboard/CLAUDE.md)
    (tokens, radius via `--radius`, icon scale, DataTable, primitives).
  2. **UX audit pass** — invoke the `ux-audit-psychology` skill (v2:
    psychology principles + clarity passes) on the surfaces you built/changed.
    Deliver the report IN CHAT per the skill's Step 6 — do not write report
    files unless the user explicitly asks to save.
  3. **Layout-composition pass** — when the work built or RESTRUCTURED a
    layout (a new page, panel, multi-zone or drag-and-drop surface — not
    copy-only or single-control changes), also invoke the
    `ux-audit-composition-layout` skill (S1 regions/enclosure, S2 spacing
    hierarchy, S3 weight/affordance, S4 stability). Same chat-only report
    rule. The three audits route findings to each other by the boundary
    tables in the two skills — never double-report one finding.

  Skip all only for pure backend changes with no user-facing surface.
