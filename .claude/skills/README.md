# abc-skills

Single source of truth (SSOT) for the ABC LEGACY UX audit skill family.
Agent Skills format — the same SKILL.md runs on claude.ai, Claude Code and the API.

| Skill | Reads |
|---|---|
| ux-audit-psychology | words & flows (P1-P6, Part C, Part T) |
| ux-audit-composition-layout | geometry (S1-S5, Part R) |
| ux-audit-compliance-design-system | token compliance (Part V) |
| ux-audit-performance-interaction | time / main thread (M1-M9, Part O, tools/measure.mjs) |
| ux-audit-disclosure-depth | depth beneath each value (D1-D6, depth ladder R0-R4, references/patterns.md) |
| ux-sourcing-component | how to obtain components (Build ladder) |

## Flow

edit -> `./validate.sh` -> commit & push -> **server:** `git pull` (Claude Code
reads live) -> **claude.ai:** re-upload the changed skill via the Save/Update
dialog (manual deploy — no API exists for the account skill library).
claude.ai auditor sessions fetch this repo read-only to verify freshness.

## Server install (Claude Code, machine-wide)

```bash
git clone https://github.com/AlonurKomilov/skills.git ~/abc-skills
mkdir -p ~/.claude/skills
for d in ~/abc-skills/ux-*/; do ln -s "$d" ~/.claude/skills/$(basename "$d"); done
```

Claude Code follows symlinks; project-specific skills stay in each repo's
`.claude/skills/`. Update everywhere with `git -C ~/abc-skills pull`.

## Rules

- `./validate.sh` must pass before commit: strict-YAML frontmatter,
  description <= 1024 bytes (single line, YAML-safe — no `: ` colon+space
  inside plain scalars), name format, folder == name, name == taxonomy.
- Method layer only in skills; project values (budgets, tokens) live in each
  project's CLAUDE.md. `./validate.sh` greps every `.md` inside a skill
  folder (SKILL.md and `references/`) for a short list of project/domain
  nouns and fails on a hit — skills audit CLASSES and EVIDENCE, never named
  things.
- Bundled resources live beside the skill (`tools/`, `references/`) and are
  read lazily at the step that needs them; a skill names the fallback (fetch
  from this repo) for platforms that upload SKILL.md alone.
- Add-only edits, shown as diffs; every patch cites source evidence (field
  runs or taught material). One mechanism = one finding.

## Taxonomy

Every skill answers up to four questions in its frontmatter, in this order:

| Field | Question | Current values |
|---|---|---|
| `family` | Who made this? | `abc` (constant across the whole repo) |
| `domain` | Where does it apply? | `ux` today; `backend`, `infra`, … as the repo grows |
| `kind` | What does it DO? | `audit` \| `sourcing` |
| `method` | HOW — which technique? | `psychology` \| `composition` \| `compliance` \| `performance` \| `disclosure` |
| `scope` | On WHAT — which slice of that method? | `layout` \| `design-system` \| `interaction` \| `depth` \| `component` |

Fields are written in that exact order, so reading the card top to bottom
spells the name. (`name` isn't shown as a labeled field on claude.ai — the
platform surfaces it as the skill's title.)

**Naming invariant:** `name == {domain}-{kind}-{method}-{scope}`, with empty
parts dropped. `./validate.sh` enforces it mechanically.

`method` and `scope` split a technique from the slice it is applied to, so
sibling skills group by technique as the repo grows: `performance` will also
cover `load` and `query`; `compliance` will also cover `accessibility` and
`api-contract`; `composition` will also cover `typography`. Two deliberate
blanks:

- **ux-audit-psychology has no `scope`** — not because none exists, but
  because it currently carries TWO in one file (Part P motivation, Part C
  clarity). If Part C is ever split out, both gain a scope.
- **ux-sourcing-component has no `method`** — the `sourcing` kind has exactly
  one technique today (the R1–R4 ladder), so naming it would repeat the kind.
  A second sourcing technique would re-open the field.

## Which skill when

| Symptom / question | Skill |
|---|---|
| Wording or flow is confusing; motivation feels off; onboarding stalls | `ux-audit-psychology` |
| Layout "looks mixed"; can't tell where a section ends; drag targets unclear | `ux-audit-composition-layout` |
| Hardcoded colors/spacing/fonts; a value that isn't from the design system | `ux-audit-compliance-design-system` |
| Surface freezes/lags/stutters; "is it my PC or the code?" | `ux-audit-performance-interaction` |
| A detail/dashboard page "feels flat"; what should be clickable or drill-in; a reference is layered and ours is not | `ux-audit-disclosure-depth` |
| Need a UI capability — reuse, compose, build, or install? | `ux-sourcing-component` |

## Versioning

One version per skill folder, semver `vMAJOR.MINOR.PATCH`:

- **MAJOR** = the report contract (the "Framework version" in the skill's
  output format) — bumps only on breaking changes to statuses/format that
  affect cross-session rollups.
- **MINOR** = new capability: a new Part, mechanism class, pattern (e.g.
  seeded-rig), or bundled tool.
- **PATCH** = fixes, clarifications, and metadata (e.g. taxonomy fields)
  that add no capability.

One number, three places, always in the same commit as the change:

1. the frontmatter `version:` field — renders as a table row on GitHub and
   as a labeled field in the claude.ai preview; `family`/`domain`/`kind`/
   `method`/`scope`/`source` sit alongside it. The last line of every
   SKILL.md stays a version-less SSOT pointer:
   `<!-- SSOT: github.com/AlonurKomilov/skills -->`.
2. the commit message prefix: `<name> vX.Y.Z: …`
3. the claude.ai deploy — upload only from this repo, so a library copy
   always answers "which version am I?" in its own frontmatter.

`./validate.sh` extracts and prints every skill's version + taxonomy and
rejects non-strict-YAML frontmatter or a name/taxonomy mismatch; a content
change without a version bump fails review. Git log is the changelog — no
separate CHANGELOG file. Sync check = compare frontmatter versions
(library vs repo HEAD) first, then hashes when versions agree.

Baselines (2026-09-03): psychology **v2.1.0** · composition-layout **v1.1.0** ·
compliance-design-system **v1.1.0** · performance-interaction **v1.6.1** ·
disclosure-depth **v1.3.0** · sourcing-component **v1.1.0**.

## abc-lab/ — the family's kept-tooling workspace

Skills never write into an audited project's tree. Measurements and rigs are
disposable by default. When a project owner explicitly asks to KEEP a
harness/rig/script a skill created, it lives at
`abc-lab/skills/<skill-name>/<harness>/` in the project root — namespaced by
owning skill, runtime files git-ignored, a one-line README inside. Contract:
`rm -rf abc-lab` removes every trace of the family's tooling from the
project. Kept harnesses carry no credentials in their tracked code — auth
material lives only in runtime-written, gitignored files, backed by a
root-level `.gitignore` belt across the whole lab. (Convention defined in
ux-audit-performance-interaction v1.4–v1.5; applies to the whole family.)

## Migration notes

- 2026-08-21 seed: layout & design-system descriptions trimmed to fit the
  1024-byte platform limit (routing content unchanged); the performance skill
  was seeded at v1.3 after two live field runs.
- 2026-08-23: versions moved to frontmatter (`version:` + `source:` keys);
  bottom stamps reduced to the version-less SSOT pointer; the performance
  description made strict-YAML-safe ("Reads TIME:" colon -> em-dash).
- 2026-08-23: taxonomy fields added (`family`/`domain`/`kind`/`method`);
  validate.sh v4 began enforcing the name-invariant.
- 2026-08-24: first rename, to `{domain}-{kind}-{method}`:
  `ux-psychology-audit` -> `ux-audit-psychology`,
  `ux-layout-composition-audit` -> `ux-audit-layout-composition`,
  `ux-design-system-audit` -> `ux-audit-design-system`,
  `ux-interaction-performance-audit` -> `ux-audit-interaction-performance`,
  `ux-component-sourcing` -> `ux-sourcing-component`.
- 2026-08-24: `scope` field added and three names corrected, because three
  `method` values were really technique+slice fused into one label:
  `ux-audit-layout-composition` -> `ux-audit-composition-layout`,
  `ux-audit-design-system` -> `ux-audit-compliance-design-system`,
  `ux-audit-interaction-performance` -> `ux-audit-performance-interaction`.
  validate.sh v5 enforces the four-part invariant with optional parts.
  Also fixed here: `tools/measure.mjs` still carried the pre-rename skill
  name in its header comment — non-markdown files were missed by the first
  rename pass, so the check now covers `.md`, `.sh` and `.mjs` alike.
- 2026-09-03: sixth member seeded — `ux-audit-disclosure-depth` v1.0.0
  (method `disclosure`, scope `depth`): scores every rendered value on six
  evidence-backed factors and recommends the lowest depth rung. validate.sh
  v6 adds the project-noun guard. Sibling cross-references to the new skill
  are deferred until its first field run.
- 2026-09-03: `ux-audit-disclosure-depth` v1.1.0 after its first field run
  (a live detail page, no repo): D3 is `?` by default and a judged D2 is
  `?` not `1` (the run printed precise scores over admitted guesses);
  tooltips are `interactive`, never `hint`; census gains an `Answers`
  column so a wrong-question path still yields INTERACT; new `SERVED`
  verdict for questions already answered in-glance on the same surface
  (the run had to footnote this); duplicates route to psychology T5;
  ethics gate names absent-data-as-favourable-value.
- 2026-09-03: `ux-audit-disclosure-depth` v1.2.0 after its second and third
  field runs. From the settings surface: hidden-state census rows tagged
  `[state: <trigger>]` with state-conditional verdicts (the run had to
  annotate these by hand); embedded values split from their sentence;
  answer-shape catalog in Step 4 — hidden question × class → what the
  disclosure must contain, form-agnostic, seeded only from field evidence.
  From the tabular reports surface: controls (sort, export, pager, help,
  assistant entry) are not census rows unless they pose as a datum's path;
  D1 integrity check — a field that disagrees with its counterpart on
  another surface is DEGRADED and carries a "not until reconciled"
  constraint (the run had to invent this); ethics mirror case extended to
  dash-columns, blank error tables and short-count summaries; optional
  "Governing finding" line before the census.
- 2026-09-03: `ux-audit-disclosure-depth` v1.3.0 — pattern library
  `references/patterns.md`: 40 patterns (25 verified in family runs, 15
  catalogued from literature and one reference intake) keyed by hidden
  question × class, prerequisites from a closed ten-primitive vocabulary,
  anti-fits, two tiers; Step 4 reads it lazily with a three-step fallback
  (folder → raw SSOT fetch → answer-shape table only, declared in the header).
  validate.sh v7 extends the noun guard to resource files and rejects any
  prerequisite outside the vocabulary.
