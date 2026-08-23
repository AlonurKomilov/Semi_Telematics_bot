---
name: ux-psychology-audit
description: Full UX audit of any user-facing feature, flow, screen, or service — TWO parts in one run. Part P audits behavioral psychology (Smart Defaults, Goal Gradient, Reciprocity, IKEA Effect, Loss Aversion, Contrast Effect + ethics gate). Part C audits clarity for a NEW user (C1 object map/OOUX, C2 component grammar, C3 cognitive walkthrough). On explicit request ("deep audit", "component by component", "tree audit", "audit every word/element") switch to Part T — audit every node of the component tree (wording, control choice, visual form, behavior, sibling consistency), then synthesize the whole image. Use when the user asks for a "UX audit", "psychology review", "clarity audit", "UX check", says a screen is confusing / not understandable / components look alike, or when wrapping up a user-facing feature and project instructions call for a UX pass. Works on ANY surface in any product domain; can audit the live rendered UI when browser/screenshot tools exist. Report delivered IN CHAT (no files by default).
---

# UX Audit — psychology + clarity

One invocation runs BOTH parts on every surface in scope:

- **Part P — Behavioral psychology** (6 evidence-based principles): does the
  surface motivate honestly?
- **Part C — Clarity** (3 structural passes): can a NEW user understand the
  surface at first sight, without anyone explaining it?

Any AI session can run this on whatever it built or touched — the output
format is standardized so reports from parallel sessions stay comparable.

North star for every finding: after the fix, the user's next action should
feel obvious, valuable, and worth completing.

> **Boundary:** neither part audits visual design-system compliance. Tokens,
> spacing, radius, icon scale, and component reuse belong to the
> `ux-design-system-audit` skill, which discovers the project's own
> design docs and token sources and audits against those.
> Part C's "grammar" findings are about MEANING collisions (a status label
> shaped like a button), not token values — if a finding is fixable by
> changing a token, it belongs in that audit, not here.
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
Users should never face a blank decision when a sensible pre-filled choice exists. Blank forms and unconfigured states create decision fatigue; a good default reads as an expert recommendation. In most products 70–90% of users never change the defaults — for the majority, the default *is* the final decision, so its honesty matters double.
- Audit questions: Does any form/setting start empty when a most-common value exists? On first run, does the user see value before configuring anything? Are the defaults the *safe and honest* choice, or the choice that benefits the business? Does the form ask anything it could skip entirely — a question most users leave untouched or the system can infer? The strongest default is no field at all.
- Typical fixes: pre-filled forms, pre-configured presets, "recommended" tier pre-selected, sample/demo data instead of empty states, CTAs that carry the waiting value ("12 results waiting" instead of "Search"), curated shortlists with a "view all" escape hatch (a few honest options beat a wall of choices), dropping or folding non-essential fields behind an "optional details" link.
- Dark-pattern line: pre-checking paid add-ons, hidden opt-ins, defaults that share data the user didn't expect.
- Evidence to cite: choice overload (Columbia jam study: 24 options → 3% purchase rate, 6 options → 30%); 70–90% default retention.

### P2. Goal Gradient (never start at 0%)
Motivation increases as people feel closer to a goal. Progress that starts above zero — even if the first steps were trivial — dramatically improves completion. The starting line is a design choice, not a fact: you decide what counts as already done.
- Audit questions: Does any multi-step flow (onboarding, setup, checkout, profile completion) start at 0%? Are already-completed steps (account created, integration connected) counted and shown? Is remaining effort visible and shrinking?
- Typical fixes: "Step 2 of 5 — 40% done", checklists with the first item pre-checked by the signup itself, visible streaks/completion meters, leveled milestones that name what the next level unlocks ("you're 2 steps away" + the reward waiting there).
- Dark-pattern line: fake progress bars that don't map to real steps, endless "one more step" loops.
- Evidence to cite: endowed progress (car-wash study: a 10-stamp card with 2 pre-stamped completed at ~2× the rate of a blank 8-stamp card — same effort, different framing).

### P3. Reciprocity (give before you ask)
People feel a pull to return value they've received. Delivering something genuinely useful *before* asking for signup, payment, or data sharply increases conversion and trust. Trust also sequences the other principles: ease and momentum do little for a user who hasn't received value yet, so at first-run and pre-signup moments P3 findings usually outrank the rest.
- Audit questions: What does the user receive before the first "give us something" moment (email, card, permissions)? Is any gate placed before the user has seen real value? Could one real result (a report, an analysis, a preview) be shown pre-signup?
- Typical fixes: free first analysis/report, preview of results behind a soft gate (diagnosis free — score, top issues, what passed; gate the step-by-step fix instructions), useful tool before the paywall.
- Dark-pattern line: "free gift" that's bait for an aggressive upsell, value that's withheld again unless the user pays immediately, fully blurred results locked behind signup (the user sees that value exists but receives none of it).
- Evidence to cite: Cialdini, after a career studying persuasion, counted reciprocity among the most powerful principles of influence; in-store free samples have lifted sampled-item purchases by up to ~2,000%.

### P4. IKEA Effect (invested effort = attachment)
People value what they helped build. Letting users customize, configure, or create early makes abandoning the product feel like abandoning their own work. Its lighter cousin, the endowment effect: the mere feeling of ownership raises value before any effort is invested — possessive labels and auto-personalization work from the first second.
- Audit questions: Can the user shape anything (layout, columns, rules, templates, names)? Is their configuration/work visibly *theirs* and preserved? In signup/onboarding specifically: does any creating or customizing happen *before* the account/payment ask — and does the ask screen show the user's own work (their named, styled thing) instead of a generic email-password form? Is early customization low-effort enough to not become friction (this must be balanced against Smart Defaults — offer a default AND let them tweak it)?
- Typical fixes: configurable dashboards/boards, custom rules and templates, "your setup" summaries, build-before-signup flows (the user creates and names the thing first, so the signup screen displays *their* creation and signing up = keeping their work; the ask button reads as continuation — "Continue", not "Sign up"), possessive naming from the first screen ("Your workspace", not "Workspace").
- Dark-pattern line: forcing heavy setup work purely to raise switching costs, holding user-created data hostage on export.
- Evidence to cite: self-built items are valued significantly above identical ready-made ones (Norton, Mochon & Ariely, Journal of Consumer Psychology, 2012 — the IKEA-effect study); ~10 minutes of invested setup measurably lowers abandonment.

### P5. Loss Aversion (losses loom larger than gains)
The pain of losing something is roughly twice as motivating as the pleasure of gaining it. Framing around what the user stands to lose moves action more than feature pitches. Loss framing wins through status quo bias: humans are wired to protect what they already have.
- Audit questions: Are consequences of inaction ever shown (money leaking, data unsaved, expiring benefit)? At exit/cancel/trial-end points, does the user see specifically what they will lose (their data, their configs, their history)? Are warnings about real losses (unsaved changes) present? Does any upgrade/upsell prompt sell only what the user could gain — a feature list plus a free "maybe later" — when a real stake exists that could honestly be shown instead? (If nothing changes when the user ignores the prompt, it carries no psychological weight.)
- Typical fixes: "You lost ~$X to idle time this week", "Your 3 custom boards will be deactivated", unsaved-changes guards, honest expiry reminders. Strongest form: show the user's *actual* items at risk (their files, their boards, their history), never a generic feature list. On an existing upsell, reframe rather than redesign — same product, same goal: headline the consequence rather than the offer, let the CTA protect what the user already has ("Protect My Files Now", not "Upgrade Now"), and let the decline own the real choice ("I'll Risk It") so skipping is a decision, not a free exit.
- Dark-pattern line: fake scarcity ("2 left!" when untrue), fabricated countdown timers, guilt-tripping confirm-shaming ("No, I like losing money"). A risk-owning decline stays honest only while the loss is real and the wording is neutral fact — the moment the stake is invented or the copy mocks the user, it becomes confirm-shaming.
- Evidence to cite: Kahneman & Tversky — losses ≈2× equivalent gains (Kahneman: Nobel in Economic Sciences, 2002).

### P6. Contrast Effect (context sets the price)
Nothing is judged in isolation — options are evaluated relative to what sits next to them. Order and adjacency of choices shape which one feels "obvious".
- Audit questions: In any choice set (pricing tiers, plans, options), what does the user see first, and what does that make the target option look like? Is there a deliberate anchor (a premium option that makes the middle tier feel easy)? Is the comparison honest — are the tiers really different in the way the layout implies? If a number appears with no anchor at all, what math will the user run alone ($50/mo silently becomes "$600/yr" in their head)? Unanchored numbers invite the user's worst-case comparison — supply the honest comparison instead.
- Typical fixes: ordered pricing tables (anchor high), "most popular" placement, before/after comparisons.
- Dark-pattern line: decoy options that exist only to mislead, hiding the cheaper plan, misleading unit comparisons.
- Evidence to cite: anchoring (Tversky & Kahneman); e.g. a $50 add-on reads as ~2.6% next to a $1,900 cart, but as $600/yr in isolation.

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
their head: e.g. Project, Member, Role, Rule, Report — whatever the
product's objects are) and check each object keeps
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
(e.g. "connect notifications to an external channel", "switch modes",
"narrow a report"). Walk each task step by step, asking at every step: (a) will they know
what to do next? (b) will they find the control? (c) will they understand the
feedback after acting? Every hesitation is a finding.
- Audit questions: When the surface starts unconfigured, is there a visible ordered path (step 1 → 2 → 3), or all controls at once? After a mode/state switch, does the layout keep a shared skeleton so learning transfers, or does the page rebuild? Is feedback after each action immediate and specific? Are error/edge states explained in task language ("the connected account lacks admin rights in the target channel") rather than system language? Do empty states name the CONSTRAINT that emptied them (an active filter, a narrowed date window, a scope switch) and the remedy — "nothing here" is false when one widening away there is plenty, and it sends someone hunting an old record away believing it's gone?
- Typical fixes: numbered setup checklist visible until configured; shared layout skeleton across modes; disable-with-reason instead of hide; success feedback that names what changed; empty-state copy that states the active constraint and how to widen it.

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
     "Single view / Split view" scans; "Single view / Split view per
     role" doesn't)? Detail belongs in the description line, never the
     label. Placeholders and hints fit without truncation at real
     widths.
   - **T2 Control choice** — is this primitive the right one for the
     interaction (two exclusive modes → option cards vs segmented
     toggle vs radios; pick-many → chips vs checkboxes)? Name the
     strongest alternative and say why the current one stays or goes.
     The alternative is drawn from the full UX vocabulary, not the
     project's current inventory — when the right control doesn't
     exist in the project yet (a raw URL doing a button's job, an
     inline paragraph that belongs in an info-tip), propose it anyway
     and let the `Build` tag carry the cost: `new-component` /
     `new-dependency` findings hand off to `ux-component-sourcing`.
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
   - T1 Wording — OK | ISSUE: <finding + concrete fix> `Impact · Effort · Build`
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

Canonical fakes to flag on sight: fake urgency, fake scarcity, fake progress, fake results, fake reviews.

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
- **[P<n>|C<n> <Principle/Pass — STATUS]** `[code|ui|code+ui]` <1–3 sentences: what exists / what's missing / exact proposed change. For OPPORTUNITY/CONFUSION: concrete, implementable suggestion. For DARK-PATTERN-RISK: the risk + ethical alternative.> `Impact: high|med|low · Effort: S|M|L · Build: existing|compose|new-component|new-dependency`

## Grammar rules proposed (C2 outputs that should become design-system law)
- <rule>

## Top actions (highest impact first, parts merged)
1. ...

## NEEDS-CONTEXT items
- <what couldn't be judged and what's needed to judge it>
```

Rules for the report:
- Every `OPPORTUNITY` and `CONFUSION` must include a change concrete enough to implement without further discussion, and carries an `Impact: high|med|low · Effort: S|M|L · Build: existing|compose|new-component|new-dependency` tag. Impact/Effort drive ranking and aggregation. Build states what the fix is made of: `existing` = components already in the project, `compose` = assembled from existing primitives, `new-component` = a new design-system component must be built, `new-dependency` = an external library is needed. The audit only classifies — it never picks a library; `new-component`/`new-dependency` items hand off to the `ux-component-sourcing` skill at implementation time.
- Build tags are hypotheses with a lifecycle, not a growing backlog: the newest report for a surface supersedes its previous findings — fixed or no-longer-needed items simply don't reappear, taking their tags with them; `ux-component-sourcing` verifies every `new-*` tag against the real inventory and downgrades over-classified ones (R1/R2 pass = tag corrected, finding closed); never carry a `new-*` tag from an old report without re-verifying — the component may exist by now.
- The `Build` tag lives in the report ONLY — it is never written into source code as a comment. `new-component`/`new-dependency` findings double as the note for the next session: repeat them at the end of Top actions as "→ ux-component-sourcing" items, so a later repo session can start from a pasted report — or simply from a re-run of this audit, which regenerates any still-unfixed items.
- No principle and no clarity pass may be skipped for any surface — `N/A` with a reason is fine; silence is not.
- Top actions merge Part P and Part C into ONE ranked list — the reader should not have to weigh two lists.
- Keep findings terse. This is an engineering artifact, not an essay.
- When a Part P `OPPORTUNITY`'s impact needs justification, cite that principle's "Evidence to cite" line — it makes recommendations persuasive without padding.
- If the user asks, produce the report in their language (e.g., Uzbek); keep statuses and principle/pass names in English so reports stay comparable.

## Step 6 — Deliver the report IN CHAT (markdown, never a report file)

Post the FULL report (Step 5 format) directly in the chat reply as markdown.
Do **not** write report files and do **not** invent a reports directory in
the repo — dated audit files accumulate into stale clutter nobody reopens,
and the project should stay clean. The chat markdown IS the deliverable;
the user copies it out if they want to keep it.

If the user explicitly asks for a file, write exactly ONE at the path THEY
name — never a default location invented by the skill. For long-term
tracking, prefer converting `OPPORTUNITY`, `CONFUSION`, and
`DARK-PATTERN-RISK` items into real issues/tickets in whatever tracker the
project uses, and let the report itself stay ephemeral.

## Aggregation mode

If the user asks for a rollup: read the reports the user pastes or points at, plus any delivered in the current chat session; merge summary tables, deduplicate findings that touch the same surface (the newest report per surface is authoritative — its older findings are superseded), re-rank all `OPPORTUNITY`, `CONFUSION`, and `DARK-PATTERN-RISK` items into one platform-wide priority list using their Impact/Effort tags, drop `new-component`/`new-dependency` items whose finding is already fixed in the current working tree (stale tags are noise — spot-check before batching), collapse findings that need the same capability into one sourcing item, group `Build: new-dependency` items together so external-library decisions are made once (dependencies overlap and interact), and list conflicting findings explicitly rather than silently picking one. Union the "Not yet audited" lists (minus anything since covered) so the next Mode B session knows where to resume. Note: reports from PAST sessions are recoverable only if the user pastes or points at them — aggregation covers what is provided plus the current conversation; say so in the rollup header instead of implying full coverage.

<!-- SSOT: github.com/AlonurKomilov/skills · 2026-08-21 -->
