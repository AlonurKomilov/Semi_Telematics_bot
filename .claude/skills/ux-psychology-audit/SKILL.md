---
name: ux-psychology-audit
description: Audit any user-facing feature, flow, screen, or service against 6 core UX psychology principles (Smart Defaults, Goal Gradient, Reciprocity, IKEA Effect, Loss Aversion, Contrast Effect) plus an ethics check. Use this skill when the user asks for a "UX audit", "psychology review", "UX check", "behavioral review", to "check the UX of" an onboarding/pricing/form/dashboard flow, or to audit the whole project/codebase UX — or when wrapping up work on a user-facing feature and the project instructions call for a UX pass. Works on ANY feature type — forms, onboarding, pricing pages, dashboards, notifications, empty states, settings, reports, CTAs — regardless of product domain; can also audit the live rendered UI when browser/screenshot tools are available. Produces a standardized report so audits from parallel sessions can be merged and compared.
---

# UX Psychology Audit

A universal, domain-agnostic framework for auditing user-facing work against six evidence-based behavioral principles. Any AI session can run this on whatever it built or touched — the output format is standardized so reports from many sessions can be aggregated.

> **Boundary:** this audits *behavioral psychology*, not visual design-system
> compliance. Tokens, spacing, radius, icon scale, and component reuse are a
> separate audit against [interfaces/dashboard/design.md](../../../interfaces/dashboard/design.md)
> and [interfaces/dashboard/CLAUDE.md](../../../interfaces/dashboard/CLAUDE.md).
> Don't report design-token violations here, and don't report psychology
> findings there.

## Scope modes (read first — pick exactly one)

Determine the audit scope before doing anything else.

**Git usage:** git is an optional helper, not the audit target. The primary source of truth is always the actual files in the working tree as they exist right now. You may use git to *locate* scope (e.g., `git diff`/`git status` to find files changed this session in Mode A, or recently touched surfaces in Mode B) — but every finding must come from reading the current file contents, never from commit history, old diffs, or commit messages. Do not spend time walking git history; if git output and the working tree disagree, the working tree wins.

**Mode A — Session scope (default when you built something this turn/session).**
Audit only the features/screens/flows you created or modified in this session (`git status`/`git diff` is a fine way to list them). Ignore the rest of the repo.

**Mode B — Project scope (default when the user says "audit the project/codebase/platform" or runs the skill in a fresh session with no prior work).**
Audit the user-facing surfaces of the entire project by reading the source files directly:
1. Discover surfaces by walking the project tree (respect .gitignore; skip node_modules, build output, tests): routes/pages (`pages/`, `app/`, `src/routes`, router configs), UI components (forms, modals, tables, dashboards, empty states), templates (email, notification, Telegram/bot messages), pricing/plan/billing screens, onboarding/setup flows, settings screens, user-facing error messages and copy.
2. Read the actual component/template code and its copy — do not infer behavior from file names alone.
3. If the project is large, audit the highest-traffic surfaces first (entry/onboarding, main dashboard, pricing, core workflow screens) and list unaudited surfaces at the end of the report under "Not yet audited" so a later session can continue.

**Mode C — Targeted scope.**
The user names specific files, folders, or features — audit exactly those, nothing more.

**Mode D — Live UI scope (use whenever browser access is available: Chrome extension via `claude --chrome` / `/chrome`, Playwright/chrome-devtools MCP, or any screenshot capability).**
Audit the *rendered* product, not just its source. Code review alone misses what users actually experience — visual hierarchy, what's above the fold, how empty states really look, whether the "default" is actually visible. When browser tools exist:
1. Open the running app (localhost or the live URL the user names) and walk the real flows: signup/onboarding start-to-finish, main dashboard first paint, one core workflow, pricing page, one exit point (cancel/logout/trial-end if reachable).
2. Take a screenshot of each surface at its key moment and evaluate the six principles against **what is visually true on screen**, not what the code intends. (Example: code may define a progress bar, but if it renders below the fold at 0%, Goal Gradient is an `OPPORTUNITY`, not `APPLIED`.)
3. Note purely visual findings the code can't show: first-visible content, blank/empty states, loading states, what the eye lands on first in a choice set (Contrast Effect is decided by layout, not markup).
4. Do not perform destructive or irreversible actions (payments, deletions, sending messages) during the audit; use test data where input is needed.
Mode D combines with A/B/C: best practice is code audit first, then verify the top findings against the live UI and mark each finding `[code]`, `[ui]`, or `[code+ui]` in the report.

In every mode: never guess about parts of the product you haven't read. If a principle can't be evaluated without seeing another part of the system, mark it `NEEDS-CONTEXT` and name exactly what file/screen you'd need.

## Step 1 — Inventory

List every user-facing surface in scope. A "surface" = anything a human sees or interacts with: a form, a screen, a button, an email/notification, an empty state, an error message, a pricing table, a progress indicator, a report, an API-driven UI state.

For each surface, note its **user moment**: first-run / recurring use / decision point / exit point. The same principle applies differently at each moment.

## Step 2 — Audit each surface against the 6 principles

For every surface, walk through all six. For each, assign exactly one status:

| Status | Meaning |
|---|---|
| `APPLIED` | Principle is already used well — say where |
| `OPPORTUNITY` | Principle is missing and would clearly help — propose the concrete change |
| `N/A` | Principle genuinely doesn't fit this surface — say why in one line |
| `DARK-PATTERN-RISK` | Principle is used manipulatively — flag and propose an ethical fix |
| `NEEDS-CONTEXT` | Can't judge without seeing X — name X |

### 1. Smart Defaults
Users should never face a blank decision when a sensible pre-filled choice exists. Blank forms and unconfigured states create decision fatigue; a good default reads as an expert recommendation.
- Audit questions: Does any form/setting start empty when a most-common value exists? On first run, does the user see value before configuring anything? Are the defaults the *safe and honest* choice, or the choice that benefits the business?
- Typical fixes: pre-filled forms, pre-configured presets, "recommended" tier pre-selected, sample/demo data instead of empty states.
- Dark-pattern line: pre-checking paid add-ons, hidden opt-ins, defaults that share data the user didn't expect.

### 2. Goal Gradient (never start at 0%)
Motivation increases as people feel closer to a goal. Progress that starts above zero — even if the first steps were trivial — dramatically improves completion.
- Audit questions: Does any multi-step flow (onboarding, setup, checkout, profile completion) start at 0%? Are already-completed steps (account created, integration connected) counted and shown? Is remaining effort visible and shrinking?
- Typical fixes: "Step 2 of 5 — 40% done", checklists with the first item pre-checked by the signup itself, visible streaks/completion meters.
- Dark-pattern line: fake progress bars that don't map to real steps, endless "one more step" loops.

### 3. Reciprocity (give before you ask)
People feel a pull to return value they've received. Delivering something genuinely useful *before* asking for signup, payment, or data sharply increases conversion and trust.
- Audit questions: What does the user receive before the first "give us something" moment (email, card, permissions)? Is any gate placed before the user has seen real value? Could one real result (a report, an analysis, a preview) be shown pre-signup?
- Typical fixes: free first analysis/report, preview of results behind a soft gate, useful tool before the paywall.
- Dark-pattern line: "free gift" that's bait for an aggressive upsell, value that's withheld again unless the user pays immediately.

### 4. IKEA Effect (invested effort = attachment)
People value what they helped build. Letting users customize, configure, or create early makes abandoning the product feel like abandoning their own work.
- Audit questions: Can the user shape anything (layout, columns, rules, templates, names)? Is their configuration/work visibly *theirs* and preserved? Is early customization low-effort enough to not become friction (this must be balanced against Smart Defaults — offer a default AND let them tweak it)?
- Typical fixes: configurable dashboards/boards, custom rules and templates, "your setup" summaries.
- Dark-pattern line: forcing heavy setup work purely to raise switching costs, holding user-created data hostage on export.

### 5. Loss Aversion (losses loom larger than gains)
The pain of losing something is roughly twice as motivating as the pleasure of gaining it. Framing around what the user stands to lose moves action more than feature pitches.
- Audit questions: Are consequences of inaction ever shown (money leaking, data unsaved, expiring benefit)? At exit/cancel/trial-end points, does the user see specifically what they will lose (their data, their configs, their history)? Are warnings about real losses (unsaved changes) present?
- Typical fixes: "You lost ~$X to idle time this week", "Your 3 custom boards will be deactivated", unsaved-changes guards, honest expiry reminders.
- Dark-pattern line: fake scarcity ("2 left!" when untrue), fabricated countdown timers, guilt-tripping confirm-shaming ("No, I like losing money").

### 6. Contrast Effect (context sets the price)
Nothing is judged in isolation — options are evaluated relative to what sits next to them. Order and adjacency of choices shape which one feels "obvious".
- Audit questions: In any choice set (pricing tiers, plans, options), what does the user see first, and what does that make the target option look like? Is there a deliberate anchor (a premium option that makes the middle tier feel easy)? Is the comparison honest — are the tiers really different in the way the layout implies?
- Typical fixes: ordered pricing tables (anchor high), "most popular" placement, before/after comparisons.
- Dark-pattern line: decoy options that exist only to mislead, hiding the cheaper plan, misleading unit comparisons.

## Step 3 — Ethics gate (mandatory)

For every `APPLIED` and every proposed `OPPORTUNITY`, ask: **does this reduce user confusion and build real trust, or does it exploit the user?** The test: *would we be comfortable explaining this design choice to the user's face?* If not, it's a dark pattern — flag it, don't ship it. In B2B products especially, one manipulative pattern can cost the entire account relationship.

## Step 4 — Output format (do not deviate)

Produce the report exactly in this structure so parallel-session reports can be merged:

```markdown
# UX Psychology Audit Report
- Framework version: 1
- Scope mode: <A session / B project / C targeted / D live-UI (can combine, e.g. B+D)> — <one line: what was reviewed>
- Date: <date> | Auditor session: <short id or task name>
- Surfaces audited: <n> | Not yet audited: <list or "none">

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| <name> | <moment> | STATUS | STATUS | STATUS | STATUS | STATUS | STATUS |

## Findings
### <Surface name>
- **[P<n> <Principle> — STATUS]** <1–3 sentences: what exists / what's missing / exact proposed change. For OPPORTUNITY: concrete, implementable suggestion. For DARK-PATTERN-RISK: the risk + ethical alternative.> `Impact: high|med|low · Effort: S|M|L`

## Top 3 actions (highest impact first)
1. ...
2. ...
3. ...

## NEEDS-CONTEXT items
- <what couldn't be judged and what's needed to judge it>
```

Rules for the report:
- Every `OPPORTUNITY` must include a change concrete enough to implement without further discussion.
- Every `OPPORTUNITY` and `DARK-PATTERN-RISK` finding carries an `Impact: high|med|low · Effort: S|M|L` tag — this is what aggregation ranks by.
- No principle may be skipped for any surface — `N/A` with a reason is fine; silence is not.
- Keep findings terse. This is an engineering artifact, not an essay.
- If the user asks, produce the report in their language (e.g., Uzbek); keep statuses and principle names in English so reports stay mergeable.

## Step 5 — Save the report

Write the report to `docs/ux-audits/<YYYY-MM-DD>-<short-scope-slug>.md` (e.g. `docs/ux-audits/2026-07-06-carrier-intake.md`) in addition to summarizing it in chat. This directory is LOCAL-ONLY (gitignored — reports are point-in-time working papers, not source; never commit them). Saved files on disk are the input that makes Aggregation mode possible across sessions, and the "Not yet audited" list is what lets a later Mode B session continue where this one stopped. If a report for the same date+scope exists, add a `-2` suffix rather than overwriting.

## Aggregation mode

If the user asks for a rollup, read all reports in `docs/ux-audits/` (or the ones they point at): merge summary tables, deduplicate findings that touch the same surface, re-rank all `OPPORTUNITY` and `DARK-PATTERN-RISK` items into one platform-wide priority list using their Impact/Effort tags, and list conflicting findings explicitly rather than silently picking one. Union the "Not yet audited" lists (minus anything since covered) so the next Mode B session knows where to resume.
