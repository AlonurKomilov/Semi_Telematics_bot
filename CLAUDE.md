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

# Project rituals

- **After building or modifying any user-facing feature** (dashboard page,
  public form, email, bot message), run two closing audits before calling
  the work done:
  1. **Design-system pass** — check the changed frontend files against
    [interfaces/dashboard/design.md](interfaces/dashboard/design.md) and
    [interfaces/dashboard/CLAUDE.md](interfaces/dashboard/CLAUDE.md)
    (tokens, radius via `--radius`, icon scale, DataTable, primitives).
  2. **UX psychology pass** — invoke the `ux-psychology-audit` skill on the
    surfaces you built/changed and save its report per the skill's Step 5.

  Skip both only for pure backend changes with no user-facing surface.
