# abc-lab — the abc-skills family workspace

Everything a family skill KEEPS in this project lives here, namespaced
by its owner, so anyone can see which skill a harness belongs to:

    abc-lab/
    └── skills/<skill-name>/<harness>/   ← owned by that skill

Universal skill tools do NOT live here — they travel inside the skill
itself (`.claude/skills/<skill>/tools/`).  This lab holds only
project-specific kept artifacts; other kinds of keeps may join later
as `abc-lab/<name>/`.

Current contents:
- `skills/ux-interaction-performance-audit/perf-rig/` — the seeded
  Dispatch-KPI board for performance A/B runs.

**Deleting this entire folder (`rm -rf abc-lab`) removes every trace
of the family's tooling from the project, always.**  Nothing else in
the project depends on it.
