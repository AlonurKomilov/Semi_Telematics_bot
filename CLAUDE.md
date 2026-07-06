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
