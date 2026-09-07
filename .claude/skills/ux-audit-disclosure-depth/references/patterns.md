# Disclosure pattern library

Companion resource of `ux-audit-disclosure-depth`. The skill decides
WHETHER a value deserves depth (D1–D6) and HOW DEEP (R0–R4); this
library lets the auditor propose WHICH pattern, with a fit score the
user can check — without the user having to know pattern names.

**Universality rule (inherited).** No domain nouns anywhere in this
file. Patterns are keyed by hidden question × value class ×
prerequisites drawn from a CLOSED vocabulary. "Seen in" entries
describe surfaces by class, never by product or field name.

**Two tiers.**
- `verified` — proposed or observed in a field run of this family;
  the run is named.
- `catalogued` — taken from published pattern literature or a
  reference product, not yet tested in a family run. The auditor may
  propose it, and says which tier it is. A field run that exercises
  it promotes it.

---

## 1. Prerequisite vocabulary (closed set — patterns may use only these)

Every prerequisite is a fact about DATA, verifiable from the payload,
the schema or the code. The auditor marks each ✓ / ✗ / `?`.

| Primitive | Holds when… | How the auditor verifies |
|---|---|---|
| `has_threshold` | the product defines a band, limit, cap or bounded range for this value | alert rule, config key, code constant, min/max in schema |
| `has_history` | past values of this value are stored and reachable | series endpoint, history table, cached samples; note the point count and window |
| `has_timestamp` | the value carries its own time of measurement or change | a `*_at` / `*_time` field beside it, not a page-load time |
| `is_cached` | the value is served from a cache or a scheduled job, not computed on request | cache headers, job name, `checked_at` older than the request |
| `has_actor_log` | changes to this value are recorded with actor and time | audit-log entries, `updated_by`, event stream |
| `has_breakdown` | the total's constituent parts exist as fields | sibling fields that sum or partition the total |
| `has_members` | a count's members exist as an array or a queryable set | `items[]` beside the count, or a filterable attribute on rows |
| `has_target_surface` | the referenced object has its own route, page or external URL | route table, link builder, external-id + base URL |
| `has_baseline` | a comparison value exists — prior period, peer set, or an expected value | prior-window field, aggregate endpoint, config expectation |
| `has_action` | an action is already wired to this value or its object | button, mutation, handler reachable from the same row/record |

A prerequisite outside this list is a request to extend the
vocabulary, not a pattern entry — propose it separately with the
evidence that no existing primitive covers it.

---

## 2. Pattern schema

```
id            short-kebab, unique
tier          verified | catalogued
question      one of the skill's catalog questions (Step 4)
class         value classes the pattern rides on
shape         what the disclosure contains (form-agnostic)
rung          R0–R4 (usual)
requires      1–3 primitives from §1 (all must hold for full fit)
anti-fit      when the pattern misleads even if prerequisites hold
seen in       field run n / reference intake / literature (source)
```

**Fit** = satisfied `requires` ÷ total `requires`; a `?` primitive
makes fit a range. Any `anti-fit` hit → `SKIP` regardless of fit.
The widget that realises a shape is not decided here (psychology T2).

---

## 3. Patterns

### A · "is this still true / as of when?"

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `age-stamp` | verified | status, level | relative age beside the value ("18h ago"); absolute date once older than ~7 days | R0 | `has_timestamp` OR `is_cached` | value refreshes every few seconds (age is noise); no timestamp at all | run 1 (readings card), run 2 (cached health badge 18.9h old, no age shown); literature: relative-time conventions |
| `stale-badge` | verified | status, level | explicit "stale" marker when age exceeds a limit, distinct from the age number | R0 | `has_timestamp`, `has_threshold` (staleness limit) | no agreed staleness limit — then use `age-stamp` only | run 1 (a warn dot on a 9h-old reading, meaning unexplained) |
| `live-indicator` | catalogued | level, status | a "live" mark while a stream/refresh is active, replacing the age | R0 | `has_timestamp` + volatility D4=2 | polling slower than the user's glance (a "live" that isn't) | literature: dashboard conventions |

### B · "is this normal here?" (threshold / band)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `banded-value` | verified | level | the value plus its band state (low / ok / high) as text or icon, colour as reinforcement only | R0 | `has_threshold` | colour with no text/icon (route to compliance); no threshold → decoration | run 3 (coloured levels with no path, no band named); reference intake |
| `threshold-ring` | catalogued | level | value inside an arc showing position within a bounded range, band-coloured, end labels | R0 | `has_threshold` (bounded range), `has_timestamp` | `counter` class; unbounded measures; more than ~6 rings per card (reads as decoration) | reference intake: a readings card of six bounded levels, one shared age line under all six |
| `warning-mark` | verified | level, status | an icon beside the value when its band is breached; hover names the band and the limit | R0 | `has_threshold` | icon without a reachable meaning; every value carrying one (no contrast) | run 1 (coloured dots, meaning never stated); reference intake |
| `progress-to-limit` | catalogued | counter, money, level | value shown as consumption against a cap ("7.2 of 10") with remaining | R0 | `has_threshold` (cap) | caps that change silently; no cap → this is a plain counter | literature: quota / allowance displays |

### C · "is it going up or down?" (trend)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `delta-chip` | verified | level, counter, money | change over the natural window ("+9,007 / 30d", "−12% vs prior") | R0 | `has_history` OR `has_baseline` (prior period) | window not stated; delta on a value whose direction has no meaning | run 1 (running total with its 30-day delta available but unrendered); run 3 (summary tiles with prior-period deltas) |
| `direction-indicator` | verified | level | rising / falling / steady only, no magnitude | R0 | `has_history` (2 points suffice) | a single reading (no direction exists) | run 3 (a level that arrives as a bare number) |
| `sparkline-tile` | verified | level, counter | word-sized series beside the summary figure, no axes | R0 | `has_history` (≥ ~5 points in window) | fewer than ~5 points (a line through noise); mixed units on one line | run 1 (17-row daily series loaded, six tiles summarising it, none plotted); literature: Tufte, sparklines |
| `hover-series` | verified | level, counter | small chart with the last N readings on hover/tap | R1 | `has_history` | touch-only contexts (no hover); series already plotted beside the value (SERVED) | run 1; literature: Tidwell, Datatips |

### D · "what is this made of?" (breakdown / members)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `inline-split` | verified | counter, money, level | total with its parts in one line ("141h drive · 217h idle") | R0 | `has_breakdown` | more than ~3 parts (use `expand-breakdown`) | run 1 (drive/idle), run 2 (a token total with prompt/reply/thinking split in the payload) |
| `stacked-bar` | catalogued | counter, money | proportion bar of the parts, labelled | R0–R1 | `has_breakdown` (parts partition the total) | parts that overlap or don't sum; tiny shares that vanish | literature: part-to-whole conventions |
| `expand-breakdown` | verified | counter, money | row/card opens to a list of parts with their values | R2 | `has_breakdown` | no expand primitive in the project (then R1 popover) | run 2 |
| `count-members-popover` | verified | counter | a count opens the list of the things it counts | R1 | `has_members` | members degraded relative to another surface (integrity rule) | run 3 (a count of 7 whose 7 items were fetched and discarded) |
| `count-chip-filter` | verified | counter | summary counts act as filters on the grid beneath | R0→filter | `has_members` (rows carry the attribute) | counts that don't sum to the grid (name the remainder first) | run 3 (critical/low/good counts above a grid); family observation: count chips on a list page |

### E · "who set this, and when?" (provenance)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `last-changed-line` | verified | status (a setting), text | "set by ‹actor› on ‹date›" under the control | R0 | `has_actor_log` | settings that change by system, not by people (say "system") | run 2 (a routing setting changed 14 times, no trace on the surface) |
| `change-history-list` | catalogued | status, text | last N changes with actor, old → new, time | R2 | `has_actor_log` | logs without old/new values (then `last-changed-line` only) | literature: audit-trail conventions |

### F · "how long has it been like this?" (duration / dwell)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `dwell-inline` | verified | status | the current state plus how long it has held ("idle 8h", "here since 06:10") | R0 | `has_history` (state transitions) OR `has_timestamp` (state start) | states that flap every few seconds | run 1 (an engine-state badge with 166 hourly rows behind it) |
| `state-timeline` | catalogued | status | strip of state segments over the window, hover for each | R1–R2 | `has_history` | no legend (route to composition); windows longer than the eye can read | run 1 (an activity chart existed, unlabelled); literature: Tidwell, Overview Plus Detail |

### G · "what's the rest of it?" (reference → object)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `entity-link` | verified | reference | the reference is a link to its own surface, keyed by fields already in the row | R0 (link) | `has_target_surface` | ambiguous keys (two objects share a name — carry the disambiguator) | run 3 (governing finding: 35 references, route existed, no link) |
| `hover-preview-card` | catalogued | reference | hover shows the target's key facts without leaving the surface | R1 | `has_target_surface`, target summary fields loaded or cheaply fetchable | touch-only; the card would repeat what the row shows | literature: Tidwell, Preview / Hover Tools; tooltip→popover ladder |
| `open-in-source` | verified | reference (external system) | link out to the system of record | R0 (link) | `has_target_surface` (external URL) | two controls to the same destination in one card (keep one) | run 1 (source card), run 2 (a duplicate link demoted) |

### H · "what happens next, and by when?"

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `next-date-inline` | verified | timestamp, status (hidden state) | the computed date of the next event ("erased on 2 Dec") | R0 | `has_timestamp` (start), `has_threshold` (rule/window) | when the rule is not stored (then state the window) | run 2 (a 90-day window with `purge_at` in the payload, never rendered) |
| `countdown` | catalogued | timestamp | time remaining with an urgency band | R0 | `has_timestamp`, `has_threshold` (urgency bands) | deadlines weeks away (show the date); countdowns that create false urgency (ethics gate) | literature: deadline displays |

### I · "clean right now, or clean all week?" (all-clear with window)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `windowed-all-clear` | verified | status (all-clear) | "none active · N in the last ‹window›" | R0 | `has_history` | no history stored (then say "as of now") | run 1 (a green all-clear above two open items from the last 48h) |
| `all-clear-last-event` | catalogued | status (all-clear) | "none · last one ‹age› ago" | R0 | `has_history` OR `has_timestamp` (last event) | — | literature |

### J · "what else is wrong, who has it? / can I do something?" (action)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `inline-action` | verified | status, reference | the action already tied to the object, beside the value | R0 | `has_action` | write actions on a read surface without confirmation (ethics/safety) | run 3 (a create action per item on the detail surface, absent from the table) |
| `acknowledge-state` | verified | status | who is on it and since when, beside the status | R0 | `has_action`, `has_actor_log` | — | run 1 (alerts carrying `acknowledged_by` / `working`, none rendered) |
| `triage-expand` | catalogued | status | expand to the related items with severity, age, owner, action | R2 | `has_members`, `has_action` | lists longer than a screen (then R3/R4) | run 1 (proposed, not yet built) |

### K · absent data (ethics mirror — the honest forms)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `empty-state` | verified | any | "no ‹things› in ‹window›" instead of 0 / dash / blank | R0 | none (value absent) | an empty state that hides a real zero (a true zero is a value) | run 2, run 3 |
| `not-reported` | verified | counter, level | "not reported" when the measurement failed while events occurred | R0 | none | — | run 2 (551 requests, 0 tokens) |
| `partial-coverage-chip` | verified | counter, level (aggregates) | "Partial · 17 of 30 days", hover explains | R0–R1 | `has_timestamp` (coverage bounds) | — | run 1 (the one good example on the surface) |
| `remainder-line` | verified | counter (summary) | "· 2 not reporting" when a summary doesn't cover its population | R0 | none | — | run 3 (4+11+83 = 98 of 100) |

### L · "is this normal here?" (comparison / baseline)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `vs-baseline-inline` | catalogued | level, money | "40% · typical 52%" — the expected value beside the actual | R0 | `has_baseline` | baselines from tiny samples; comparing across unlike peers | literature: dashboard conventions |
| `peer-rank` | catalogued | level, counter | "3rd of 90" within the peer set | R0 | `has_baseline` (peer set) | peer sets under ~10; rank without the metric | literature |
| `normal-band-from-history` | catalogued | level | "typical 35–45%" derived from the value's own history | R0 | `has_history` (enough to compute a band) | short or seasonal histories | literature: control-chart intuition |

### M · `text` and `identifier` (mostly utility, not depth)

| id | tier | class | shape | rung | requires | anti-fit | seen in |
|---|---|---|---|---|---|---|---|
| `truncate-expand` | catalogued | text | truncated prose with "more" | R2 | none | critical text (never truncate what must be read) | literature |
| `full-text-tooltip` | catalogued | text, reference (labels) | truncated label reveals the full string on hover | R1 | none | touch-only | literature: list UIs |
| `copy-action` | verified | identifier | copy to clipboard; not a disclosure — routed to psychology T2 | — | none | — | run 1 (coordinates had it, the serial did not) |

---

## 4. How the auditor uses this (Step 4 hook)

For every `INTERACT` and argued `CANDIDATE`:

1. Take the hidden question → open the matching family (A–M).
2. For each pattern in the family whose `class` includes the value's
   class: verify each `requires` primitive (✓ / ✗ / `?`) from the
   source; check `anti-fit`.
3. Report the top one or two:
   `Pattern: sparkline-tile (fit 1/1, verified) · alt: hover-series
   (fit 1/1)` — or `no pattern fits: has_history ✗ — data first`.
4. A `catalogued` pattern is proposed with its tier named; the user
   knows it is untested in this family.

The pattern names the CONTENT and the usual rung; the project's own
vocabulary (Step 0b) names the primitive; psychology T2 names the
widget.

---

## 5. Intake rules (how this file grows)

- A new entry needs: a question from the skill's catalog, ≥1 class,
  ≥1 primitive from §1, an `anti-fit`, and a `seen in`.
- From a **field run**: cite the run; tier `verified`.
- From a **reference product**: describe the surface by class ("a
  readings card of six bounded levels"), never by product or field;
  tier `catalogued` until a run exercises it.
- From **literature**: cite the source; tier `catalogued`.
- Promotion: a run that proposes or observes a `catalogued` pattern
  moves it to `verified` with the run cited.
- A primitive missing from §1 is proposed on its own line with the
  evidence that none of the ten covers it. The vocabulary grows
  slowly on purpose.
- The project-noun guard applies to this file.
