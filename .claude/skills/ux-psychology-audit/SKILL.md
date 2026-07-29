---
name: ux-psychology-audit
description: Full UX audit of any user-facing feature, flow, screen, or service — TWO parts in one run. Part P audits behavioral psychology (Smart Defaults, Goal Gradient, Reciprocity, IKEA Effect, Loss Aversion, Contrast Effect + ethics gate). Part C audits comprehension clarity for a NEW user (C1 object map / OOUX coherence, C2 component grammar / interface inventory, C3 cognitive walkthrough of first-run tasks). On explicit request ("deep audit", "component by component", "tree audit", "audit every word/element") switch to Part T — a component-tree deep audit that decomposes the target into its component tree and audits every node (wording, control choice, visual form, behavior, sibling consistency) before synthesizing the whole image. Use when the user asks for a "UX audit", "psychology review", "clarity audit", "UX check", says a screen is confusing / not understandable / components look alike, or when wrapping up work on a user-facing feature and the project instructions call for a UX pass. Works on ANY surface type in any product domain; can also audit the live rendered UI when browser/screenshot tools are available. Report is delivered IN CHAT (no files by default).
---

# UX Audit — psychology + clarity

One invocation runs BOTH parts on every surface in scope:

- **Part P — Behavioral psychology** (6 evidence-based principles): does the
  surface motivate honestly?
- **Part C — Clarity** (3 structural passes): can a NEW user understand the
  surface at first sight, without anyone explaining it?

Any AI session can run this on whatever it built or touched — the output
format is standardized so reports from parallel sessions stay comparable.

> **Boundary:** neither part audits visual design-system compliance. Tokens,
> spacing, radius, icon scale, and component reuse are a separate audit against
> [interfaces/dashboard/design.md](../../../interfaces/dashboard/design.md)
> and [interfaces/dashboard/CLAUDE.md](../../../interfaces/dashboard/CLAUDE.md).
> Part C's "grammar" findings are about MEANING collisions (a status label
> shaped like a button), not token values — if a finding is fixable by
> changing a token, it belongs in the design pass, not here.
> **Spatial composition is the third sibling:** whether an ARRANGEMENT
> communicates structure — regions enclosed, between-group air exceeding
> within-group, source vs destination legible without reading, drop targets
> visible before a drag, layouts that hold still — belongs to
> `ux-layout-composition-audit`, not here. Route those findings there.
> (One deliberate difference: that skill's S3 overrides Part T's T5 for
> regions — sibling REGIONS playing OPPOSITE interaction roles must look
> different, even though sibling ELEMENTS share one grammar.)

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

**Mode D — Live UI scope (use whenever browser access is available: Chrome extension via `claude --chrome` / `/chrome`, Playwright/chrome-devtools MCP, screenshots the user pasted, or any screenshot capability).**
Audit the *rendered* product, not just its source. Code review alone misses what users actually experience — visual hierarchy, what's above the fold, how empty states really look, whether the "default" is actually visible. When browser tools exist:
1. Open the running app (localhost or the live URL the user names) and walk the real flows: signup/onboarding start-to-finish, main dashboard first paint, one core workflow, pricing page, one exit point (cancel/logout/trial-end if reachable).
2. Take a screenshot of each surface at its key moment and evaluate both parts against **what is visually true on screen**, not what the code intends. (Example: code may define a progress bar, but if it renders below the fold at 0%, Goal Gradient is an `OPPORTUNITY`, not `APPLIED`. Likewise C2 look-alike clusters are decided by rendered shape, not by which React component was used.)
3. Note purely visual findings the code can't show: first-visible content, blank/empty states, loading states, what the eye lands on first in a choice set.
4. Do not perform destructive or irreversible actions (payments, deletions, sending messages) during the audit; use test data where input is needed.
Mode D combines with A/B/C: best practice is code audit first, then verify the top findings against the live UI and mark each finding `[code]`, `[ui]`, or `[code+ui]` in the report. Screenshots the user pasted into chat count as `[ui]` evidence.

In every mode: never guess about parts of the product you haven't read. If a check can't be evaluated without seeing another part of the system, mark it `NEEDS-CONTEXT` and name exactly what file/screen you'd need.

## Step 1 — Inventory

List every user-facing surface in scope. A "surface" = anything a human sees or interacts with: a form, a screen, a button, an email/notification, an empty state, an error message, a pricing table, a progress indicator, a report, an API-driven UI state.

For each surface, note its **user moment**: first-run / recurring use / decision point / exit point. The same principle applies differently at each moment.

## Step 2 — Part P: six psychology principles

For every surface, walk through all six. For each, assign exactly one status:

| Status | Meaning |
|---|---|
| `APPLIED` | Principle is already used well — say where |
| `OPPORTUNITY` | Principle is missing and would clearly help — propose the concrete change |
| `N/A` | Principle genuinely doesn't fit this surface — say why in one line |
| `DARK-PATTERN-RISK` | Principle is used manipulatively — flag and propose an ethical fix |
| `NEEDS-CONTEXT` | Can't judge without seeing X — name X |

### P1. Smart Defaults
Users should never face a blank decision when a sensible pre-filled choice exists. Blank forms and unconfigured states create decision fatigue; a good default reads as an expert recommendation.
- Audit questions: Does any form/setting start empty when a most-common value exists? On first run, does the user see value before configuring anything? Are the defaults the *safe and honest* choice, or the choice that benefits the business?
- Typical fixes: pre-filled forms, pre-configured presets, "recommended" tier pre-selected, sample/demo data instead of empty states.
- Dark-pattern line: pre-checking paid add-ons, hidden opt-ins, defaults that share data the user didn't expect.

### P2. Goal Gradient (never start at 0%)
Motivation increases as people feel closer to a goal. Progress that starts above zero — even if the first steps were trivial — dramatically improves completion.
- Audit questions: Does any multi-step flow (onboarding, setup, checkout, profile completion) start at 0%? Are already-completed steps (account created, integration connected) counted and shown? Is remaining effort visible and shrinking?
- Typical fixes: "Step 2 of 5 — 40% done", checklists with the first item pre-checked by the signup itself, visible streaks/completion meters.
- Dark-pattern line: fake progress bars that don't map to real steps, endless "one more step" loops.

### P3. Reciprocity (give before you ask)
People feel a pull to return value they've received. Delivering something genuinely useful *before* asking for signup, payment, or data sharply increases conversion and trust.
- Audit questions: What does the user receive before the first "give us something" moment (email, card, permissions)? Is any gate placed before the user has seen real value? Could one real result (a report, an analysis, a preview) be shown pre-signup?
- Typical fixes: free first analysis/report, preview of results behind a soft gate, useful tool before the paywall.
- Dark-pattern line: "free gift" that's bait for an aggressive upsell, value that's withheld again unless the user pays immediately.

### P4. IKEA Effect (invested effort = attachment)
People value what they helped build. Letting users customize, configure, or create early makes abandoning the product feel like abandoning their own work.
- Audit questions: Can the user shape anything (layout, columns, rules, templates, names)? Is their configuration/work visibly *theirs* and preserved? Is early customization low-effort enough to not become friction (this must be balanced against Smart Defaults — offer a default AND let them tweak it)?
- Typical fixes: configurable dashboards/boards, custom rules and templates, "your setup" summaries.
- Dark-pattern line: forcing heavy setup work purely to raise switching costs, holding user-created data hostage on export.

### P5. Loss Aversion (losses loom larger than gains)
The pain of losing something is roughly twice as motivating as the pleasure of gaining it. Framing around what the user stands to lose moves action more than feature pitches.
- Audit questions: Are consequences of inaction ever shown (money leaking, data unsaved, expiring benefit)? At exit/cancel/trial-end points, does the user see specifically what they will lose (their data, their configs, their history)? Are warnings about real losses (unsaved changes) present?
- Typical fixes: "You lost ~$X to idle time this week", "Your 3 custom boards will be deactivated", unsaved-changes guards, honest expiry reminders.
- Dark-pattern line: fake scarcity ("2 left!" when untrue), fabricated countdown timers, guilt-tripping confirm-shaming ("No, I like losing money").

### P6. Contrast Effect (context sets the price)
Nothing is judged in isolation — options are evaluated relative to what sits next to them. Order and adjacency of choices shape which one feels "obvious".
- Audit questions: In any choice set (pricing tiers, plans, options), what does the user see first, and what does that make the target option look like? Is there a deliberate anchor (a premium option that makes the middle tier feel easy)? Is the comparison honest — are the tiers really different in the way the layout implies?
- Typical fixes: ordered pricing tables (anchor high), "most popular" placement, before/after comparisons.
- Dark-pattern line: decoy options that exist only to mislead, hiding the cheaper plan, misleading unit comparisons.

## Step 3 — Part C: three clarity passes

Part C answers one question Part P cannot: **would a brand-new user understand
this surface with nobody explaining it?** Run all three passes per surface (or
once per page when several surfaces share one screen — say which). Statuses:

| Status | Meaning |
|---|---|
| `CLEAR` | Pass finds no comprehension problem — say what carries it |
| `CONFUSION` | A concrete comprehension problem — propose the exact fix |
| `N/A` | Pass genuinely doesn't apply — one-line reason |
| `NEEDS-CONTEXT` | Can't judge without seeing X — name X |

### C1. Object map (whole-image coherence — OOUX)
List the domain objects the surface presents (the nouns a user must hold in
their head: e.g. Bot, Group, Role, Topic, Rule) and check each object keeps
ONE name and ONE visual face everywhere it appears.
- Audit questions: Does any word mean two different things on the same screen (same-word-two-meanings)? Does any object appear under two names or two unrelated visual treatments (one-object-many-faces)? Are the relations between objects visible (which bot posts to which group, which rule narrows which topic), or must the user infer them? Do section headings map 1:1 to objects/tasks, or do they overlap?
- Typical fixes: rename one of the colliding labels; merge duplicate sections; give each object one consistent row/card representation; add a one-line relation sentence ("This bot posts to: <group>").

### C2. Component grammar (interface inventory)
Collect every interactive and status element on the surface (chips, pills,
badges, buttons, toggles, links, checkboxes) and cluster look-alikes.
- Audit questions: Can the eye separate *what IS* (status) from *what I CAN DO* (action) from *what FILTERS* (selection) without reading? Do any two elements share one shape but different behavior — or one behavior but two shapes? Does every repeated list use one row template (same cell order: identity → status → actions), or does each row improvise? Is the single most important action on the surface also the most visually prominent?
- Typical fixes: one shape per meaning class (e.g. status = flat tinted chip, action = bordered button, filter = toggleable chip with selected state); one row grammar for the whole list; promote the primary action, demote secondary ones.
- Output extra: when violations are found, state the grammar RULE that fixes the whole class, not just the instance — that rule is a candidate for the design-system SSOT.

### C3. Cognitive walkthrough (new-user comprehension)
Pick 1–3 realistic first-run tasks a NEW user would attempt on this surface
(e.g. "get safety alerts into a Telegram group", "switch modes", "narrow a
topic"). Walk each task step by step, asking at every step: (a) will they know
what to do next? (b) will they find the control? (c) will they understand the
feedback after acting? Every hesitation is a finding.
- Audit questions: When the surface starts unconfigured, is there a visible ordered path (step 1 → 2 → 3), or all controls at once? After a mode/state switch, does the layout keep a shared skeleton so learning transfers, or does the page rebuild? Is feedback after each action immediate and specific? Are error/edge states explained in task language ("bot needs admin rights in the group") rather than system language?
- Typical fixes: numbered setup checklist visible until configured; shared layout skeleton across modes; disable-with-reason instead of hide; success feedback that names what changed.

## Deep mode — Part T: component-tree audit (on explicit request)

Parts P and C audit whole SURFACES. When the user asks to go deeper —
"deep audit", "component by component", "tree audit", "audit every
word/element" — switch to tree depth. This is the atomic-design idea
applied as an audit: decompose, judge every node, then reassemble the
whole image.

1. **Component census first — COLLECT everything before judging
   anything.** Walk the real component source (and the rendered
   UI/screenshots when available) and inventory every user-perceivable
   piece into seven fixed categories:
   - **Texts** — headings, labels, captions, hints, placeholders, legends
   - **Actions** — buttons, links, expanders, menu items
   - **Inputs** — fields, selects, checkboxes, filter chips
   - **Status & feedback** — chips, badges, banners, counters, toasts, confirms
   - **Structure** — sections, group headings, separators, column alignment
   - **Hidden states** — everything that appears only on a trigger:
     loading, empty, error/failure, busy, disabled, unconfigured.
     Screenshots NEVER show these — find them in the code: every
     conditional render (`&&`, ternary, early return, `catch → null`)
     is a state some user WILL eventually see.
   - **Overlays** — tooltips/toggletips, dialogs, dropdowns
   Print the census as a short table with counts, then draw the
   component tree from it (surface → sections → blocks → controls →
   atoms). The tree is the audit's table of contents; the census is
   its completeness proof. Cross-check BOTH directions before
   auditing: everything visible in the screenshot must appear in the
   census, and every conditional branch in the code must map to a
   Hidden-states entry. Any census item not covered by a card must be
   listed at the end under **"Not audited"** — a silent skip is the
   exact failure this step exists to prevent.
2. **One audit card per node**, descending layer by layer — finish a
   section's nodes before entering the next section; never jump
   around the tree. Fixed checks per card:
   - **T1 Wording** — is the label the SHORTEST accurate one for
     first-view scanning? Parallel grammar with siblings (a pair like
     "Single bot / Sub bots" scans; "Single bot / Sub bot per role"
     doesn't)? Detail belongs in the description line, never the
     label. Placeholders and hints fit without truncation at real
     widths.
   - **T2 Control choice** — is this primitive the right one for the
     interaction (two exclusive modes → option cards vs segmented
     toggle vs radios; pick-many → chips vs checkboxes)? Name the
     strongest alternative and say why the current one stays or goes.
     "Current is correct" is a valid verdict — justify it, don't
     invent change.
   - **T3 Visual form** — would an icon add recognition or just
     noise? Is prominence proportional to importance? Are all states
     drawn (hover, selected, disabled, busy, empty)? No wrapping or
     truncation at the sizes really rendered.
   - **T4 Behavior & feedback** — what exactly a click does; is
     feedback immediate and specific; are consequences visible BEFORE
     destructive or hard-to-reverse actions.
   - **T5 Sibling consistency** — same-level nodes share one grammar
     (cell order, chip shape, verb style, capitalization).

   Card format (terse, mergeable):
   ```
   ### <tree path, e.g. Mode selector → "Sub bot per role" option>
   - T1 Wording — OK | ISSUE: <finding + concrete fix> `Impact · Effort`
   - T2 Control — …
   - T3 Visual — …
   - T4 Behavior — …
   - T5 Siblings — …
   ```
   Only ISSUE lines need prose; OK may carry a ≤1-line reason.
3. **Synthesis — the whole image.** After all cards: a consistency
   matrix of repeated patterns (chips, buttons, labels) across
   branches; the surface-level Part P table (P runs once per surface —
   it is about user moments, not atoms; Part C's checks are folded
   into T1–T5); and ONE ranked Top-actions list. The synthesis is
   where component-level findings become page-level decisions.

### Splitting across sub-agents (large trees only)

Up to ~15 audit-worthy nodes: ONE session audits the whole tree —
cross-node comparison lives in one context and costs nothing extra.
Larger scope (a whole page family, several surfaces): the MAIN session
builds the tree itself, then may fan out one sub-agent per BRANCH
(never per atom), each given: the branch's file paths, the card format
above verbatim, and the T1–T5 vocabulary. Two rules survive any split:

- The main session always writes the synthesis itself — sibling
  consistency and whole-image findings are cross-branch by nature; no
  branch agent can see them.
- Branch agents return CARDS only (no prose reports), so the merge is
  mechanical and nothing is lost in paraphrase.

Fan-out spends real tokens — confirm with the user before launching
more than ~3 branch agents, and always tell them the planned split.

## Step 4 — Ethics gate (mandatory)

For every `APPLIED` and every proposed `OPPORTUNITY`, ask: **does this reduce user confusion and build real trust, or does it exploit the user?** The test: *would we be comfortable explaining this design choice to the user's face?* If not, it's a dark pattern — flag it, don't ship it. In B2B products especially, one manipulative pattern can cost the entire account relationship.

## Step 5 — Output format (do not deviate)

Produce ONE merged report in this structure so parallel-session reports stay comparable:

```markdown
# UX Audit Report (psychology + clarity)
- Framework version: 2
- Scope mode: <A session / B project / C targeted / D live-UI (can combine, e.g. C+D)> — <one line: what was reviewed>
- Date: <date> | Auditor session: <short id or task name>
- Surfaces audited: <n> | Not yet audited: <list or "none">

## Part P summary
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| <name> | <moment> | STATUS | STATUS | STATUS | STATUS | STATUS | STATUS |

## Part C summary
| Surface | C1 Objects | C2 Grammar | C3 Walkthrough |
|---|---|---|---|
| <name> | STATUS | STATUS | STATUS |

## Findings
### <Surface name>
- **[P<n>|C<n> <Principle/Pass — STATUS]** `[code|ui|code+ui]` <1–3 sentences: what exists / what's missing / exact proposed change. For OPPORTUNITY/CONFUSION: concrete, implementable suggestion. For DARK-PATTERN-RISK: the risk + ethical alternative.> `Impact: high|med|low · Effort: S|M|L`

## Grammar rules proposed (C2 outputs that should become design-system law)
- <rule>

## Top actions (highest impact first, parts merged)
1. ...

## NEEDS-CONTEXT items
- <what couldn't be judged and what's needed to judge it>
```

Rules for the report:
- Every `OPPORTUNITY` and `CONFUSION` must include a change concrete enough to implement without further discussion, and carries an `Impact: high|med|low · Effort: S|M|L` tag — this is what ranking and aggregation use.
- No principle and no clarity pass may be skipped for any surface — `N/A` with a reason is fine; silence is not.
- Top actions merge Part P and Part C into ONE ranked list — the reader should not have to weigh two lists.
- Keep findings terse. This is an engineering artifact, not an essay.
- If the user asks, produce the report in their language (e.g., Uzbek); keep statuses and principle/pass names in English so reports stay comparable.

## Step 6 — Deliver the report IN CHAT (no files by default)

Post the FULL report (Step 5 format) directly in the chat reply as markdown.
Do **not** write a report file — saved reports proved to be write-only
clutter the user never reopens.

**Exception — only on explicit request:** if the user explicitly asks to save
(says "save", or asks for a cross-session aggregation baseline), write the
report to `docs/ux-audits/<YYYY-MM-DD>-<short-scope-slug>.md` in addition to
the chat reply. That directory stays LOCAL-ONLY (gitignored working papers —
never commit them). If a report for the same date+scope exists, add a `-2`
suffix rather than overwriting.

## Aggregation mode

If the user asks for a rollup: read whatever saved reports exist in `docs/ux-audits/` (or the ones they point at), plus any reports delivered in the current chat session; merge summary tables, deduplicate findings that touch the same surface, re-rank all `OPPORTUNITY`, `CONFUSION`, and `DARK-PATTERN-RISK` items into one platform-wide priority list using their Impact/Effort tags, and list conflicting findings explicitly rather than silently picking one. Union the "Not yet audited" lists (minus anything since covered) so the next Mode B session knows where to resume. Note: chat-only reports from PAST sessions are not recoverable — aggregation can only cover what was saved or what is in the current conversation; say so in the rollup header instead of implying full coverage.
