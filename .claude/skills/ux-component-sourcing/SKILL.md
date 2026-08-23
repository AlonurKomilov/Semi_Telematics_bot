---
name: ux-component-sourcing
description: Decide how to obtain any UI capability — reuse an existing component, compose it from existing primitives, build a new design-system component, or (last resort) add an external library. ALWAYS use this skill BEFORE suggesting or installing any UI dependency (npm install of a component/UI package, adding a CDN @require, vendoring code), when implementing a fix or feature that needs a component the project might not have, when the user asks "which library should we use", "build or install?", "do we have a component for this?", or when an audit finding carries a Build tag of compose, new-component, or new-dependency. Never name a library before reading the project's actual stack — Step 0 grounding is mandatory.
---

# Component Sourcing — the implementation ladder

Purpose: pick the LOWEST rung that solves the need. "Cost" here is long-term — maintenance, bundle size, accessibility debt, lock-in — never today's minutes. The user does not need to know libraries; this skill's job is to apply the decision criteria to their real stack and bring back a grounded recommendation.

This skill decides ONE need at a time — it is not an audit and never sweeps the project. It runs standalone (a direct "build or install?" question, or the moment any UI dependency is about to be added) just as well as from an audit's `Build: new-*` handoff. A project-wide "what is duplicated or re-implemented" question belongs to `ux-design-system-audit` (D5), whose findings then arrive here one at a time.

## Step 0 — Grounding (mandatory, before ANY suggestion)

Read, in this order. Never propose a component or library before finishing all four:

1. **Dependency manifest** — whatever the stack uses: `package.json` (+ lockfile for real versions), `pubspec.yaml`, `pyproject.toml` / `requirements.txt`, `Cargo.toml`. In userscript / no-bundler projects (Tampermonkey, plain HTML), the `@require` / CDN imports in the script header are the manifest.
2. **The project's own component inventory** — `components/`, `src/components/`, `ui/`, design-system folders, Storybook if present. List everything adjacent to the need.
3. **Styling system** — Tailwind config, CSS variables, tokens file. Anything new must speak the same styling language.
4. **Project rules** — CLAUDE.md, design.md, contribution docs. They may already mandate or ban specific libraries.

Absence is an answer, not a blocker: an empty or missing component inventory means R1 fails legitimately; no styling system and no rules docs mean nothing is mandated or banned — record each absence in the Step 0 output (it is often `SSOT-GAP` material for `ux-design-system-audit`). The ONLY stop condition is a genuinely undeterminable stack: when the files present cannot tell you what the UI is built on, say so and ask — a rung-4 recommendation without a known stack is a guess.

Repo-less grounding is provisional. When Step 0 runs without source access — only the shipped bundle, the rendered app, or screenshots — say so and mark the grounding PROVISIONAL: a bundle census can miss lazy-loaded, tree-shaken, or simply-unused components, and rules docs are then UNREACHABLE, not absent. R1/R2 verdicts built on a provisional census are hypotheses, and an R3/R4 decision may be recommended but never executed until the inventory and rules are verified against the repo — write the condition into the Decision line ("awaiting approval + repo verification").

Step 0 output (one short paragraph): framework + version, styling approach, existing components adjacent to the need, constraints found, absences noted.

## The ladder — walk bottom-up, stop at the first rung that passes

**Rung 1 — Reuse an existing component.**
Passes when: something in the inventory already covers the interaction, possibly via props/variants. Small visual deltas belong in the existing component's variants — if it ALMOST fits, extend it with a variant rather than building a sibling.

**Rung 2 — Compose from existing primitives.**
Passes when: the pattern is a combination of things the project has (buttons + state = segmented toggle; input + dropdown = combobox-lite; cards + radio semantics = option cards), it's roughly an evening of work, and it needs no accessibility semantics beyond what the primitives already carry. Build it as a local component; promote to the design system only when it gets reused (rung 3's territory).

**Rung 3 — New design-system component.**
Passes when: the pattern is needed in 2+ places (or is clearly product-core), the keyboard/ARIA semantics are well understood (a documented WAI-ARIA pattern exists and is implementable), and the scope is bounded. Build it ONCE in the design-system location — tokens, all states (hover/focus/disabled/busy/empty), documented. Never fork per-screen copies.

**Rung 4 — External dependency (last resort).**
Only for genuinely hard components where correct a11y + edge cases cost person-weeks: date/date-range pickers, virtualized tables and lists, drag-and-drop, rich text editors, charting, complex comboboxes. Before naming anything, vet 2–3 candidates:

- **Maintenance** — last release date, open-issues trend, bus factor
- **Size** — bundle impact, tree-shakeability
- **Accessibility** — keyboard support, ARIA correctness, known screen-reader reports
- **License** — compatible with commercial use
- **Fit** — works with the project's framework version and styling system; prefer headless libraries when a design system exists (behavior from the library, look stays yours)
- **Lock-in** — how hard to remove later; data/format portability

In no-bundler/userscript contexts, "dependency" means a pinned CDN `@require` or a vendored single file — add criteria: available as a single UMD/IIFE build, no build step required, version pinned.

Rung 4 output: a short comparison table of the candidates + ONE recommendation + one line on why rungs 1–3 failed.

## Hard rules

- Never name a library before Step 0 is complete. "Install shadcn" without having read the stack is the exact failure this skill exists to prevent.
- Skipping a rung requires stating why its pass-criteria fail. "Faster to install" is not a reason — long-term cost beats today's minutes.
- One need = one decision. When several findings need components, decide each on its own rung, but batch all rung-4 candidates into one review — dependencies overlap and interact (two libraries solving the same problem is a smell).
- Never mix rungs in one recommendation ("install X and also build Y") without explicitly justifying both.
- Approval gate: rungs 1–2 are implementation details — just do them. Rungs 3–4 are architecture decisions — present the recommendation and WAIT for the user's approval before building or installing.
- When invoked from an audit finding (`Build: new-component` / `new-dependency`), quote the finding being implemented so the decision stays traceable.
- Downgrade is a success, not an error: when an audit-tagged `new-component`/`new-dependency` need passes R1 or R2, the tag was over-classified — state it explicitly ("tag downgraded to existing/compose — no sourcing needed") so the finding closes clean and no dependency discussion ever starts. The ladder exists precisely to catch over-classification.
- Staleness check: before walking the ladder for an audit-invoked need, confirm the finding still reproduces in the current code. If it doesn't (fixed since, surface removed, requirement changed), stop — report STALE, close the finding, source nothing.

## Output format (compact, do not deviate)

```
Need: <one line — the interaction/pattern required + where it came from (audit finding / feature request)>
Stack: <one line from Step 0>
R1 Reuse: PASS — <component> | FAIL — <why>
R2 Compose: PASS — <recipe> | FAIL — <why> | not reached
R3 New component: PASS — <scope> | FAIL — <why> | not reached
R4 Dependency: <candidates table + recommendation> | not reached
Decision: <rung + next step; R3/R4 — "awaiting approval"; audit-invoked R1/R2 pass — "tag downgraded to <value>"; no longer reproduces — "STALE, finding closed">
Routed to audits (optional): <off-need violations the census stumbled on — one line each, destination named>
```

Report rungs top-down until the first PASS; everything below it is `not reached`. A `FAIL` line must name the failed criterion, not restate the rung.

The census exists to serve rung criteria, but don't waste what it saw: off-need violations it stumbled on (re-implemented primitives, improvised controls, drifted class strings) go on the `Routed to audits` line as CANDIDATES for `ux-design-system-audit` — one line each, no scoring, no report. This is the whole extent of this skill's audit surface: it routes what it happened to see; it never goes looking.

Print this block BEFORE implementing, on every run — including R1/R2. Visibility is unconditional; only the approval gate (R3/R4) is conditional: the user always sees the ladder walk first, the same way audit findings are seen before fixes.

<!-- SSOT: github.com/AlonurKomilov/skills · 2026-08-21 -->
