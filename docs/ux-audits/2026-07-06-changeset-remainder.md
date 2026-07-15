# UX Psychology Audit Report
- Framework version: 1
- Scope mode: A session — continuation audit of the same uncommitted changeset covered by `2026-07-06-datatable-v2-ai-threads.md`; this report covers the surfaces that report did NOT audit (Payroll, Coaching, Cost Reports, Maintenance cluster + AI urgency, Vehicles, Storage queue, Settings, Audit Log, nav generation, co-owner email, role-focused briefing, chat integrity). Shared-DataTable behavior is deliberately excluded (already filed).
- Date: 2026-07-06 | Auditor session: changeset-remainder
- Surfaces audited: 18 | Not yet audited: WorkOrderDetail page, /work-orders/new wizard, maintenance CalendarMonth, ServiceHistoryModal, TemplatesModal, Telegram bot maintenance-alert copy, VehicleManageDialog (unchanged this changeset but referenced by audited surfaces)
- Mechanical-only (DataTable-prop adoption, no behavioral change, no audit needed): Cameras, Inspections, Parking, Drivers, Safety Events, Scorecards, FuelCosts, CostPerMile, Reports (per-tab tableId), Companies

## Summary table
| Surface | User moment | P1 Defaults | P2 Goal | P3 Recip. | P4 IKEA | P5 Loss | P6 Contrast |
|---|---|---|---|---|---|---|---|
| Payroll (runs/rules/settings) | recurring / decision | OPPORTUNITY | N/A | OPPORTUNITY | APPLIED | OPPORTUNITY | APPLIED |
| Coaching (assignments/rules) | recurring / decision | OPPORTUNITY | N/A | OPPORTUNITY | APPLIED | OPPORTUNITY | APPLIED |
| Cost Reports | recurring | APPLIED | N/A | N/A | OPPORTUNITY | APPLIED | APPLIED |
| Maintenance Tasks list | recurring / decision | APPLIED | APPLIED | N/A | APPLIED | APPLIED | OPPORTUNITY |
| New Task form | decision (first-run entry) | APPLIED | N/A | APPLIED | APPLIED | OPPORTUNITY | APPLIED |
| Task Edit drawer | decision | APPLIED | N/A | APPLIED | APPLIED | OPPORTUNITY | APPLIED |
| Maintenance bulk-action bar | decision | APPLIED | N/A | N/A | N/A | OPPORTUNITY | APPLIED |
| Work Orders list | recurring | APPLIED | N/A | N/A | APPLIED | APPLIED | OPPORTUNITY |
| AI maintenance urgency (answers + chips + snapshot) | recurring / decision | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |
| Vehicles list | recurring / decision hub | OPPORTUNITY | N/A | APPLIED | APPLIED | N/A | APPLIED |
| Storage sync queue | recurring (exception recovery) | APPLIED | APPLIED | APPLIED | N/A | APPLIED | APPLIED |
| Settings (hours/TZ/bot) | recurring config / decision | APPLIED | N/A | APPLIED | APPLIED | OPPORTUNITY | APPLIED |
| Audit Log | recurring | APPLIED | N/A | APPLIED | APPLIED | N/A | APPLIED |
| Navigation (cross-dept grants + briefing labels) | every session | APPLIED | N/A | APPLIED | OPPORTUNITY | N/A | APPLIED |
| Co-owner promotion code email | decision (security-charged) | APPLIED | APPLIED | APPLIED | N/A | OPPORTUNITY | APPLIED |
| Role-focused AI briefing | recurring | APPLIED | N/A | APPLIED | OPPORTUNITY | APPLIED | N/A |
| AI chat conversational integrity | recurring | APPLIED | N/A | N/A | APPLIED | APPLIED | N/A |
| ReferencedVehicles due-soon chips | recurring | APPLIED | N/A | N/A | N/A | APPLIED | APPLIED |

## Findings

### Payroll (runs / rules / settings)
- **[P1 Smart Defaults — OPPORTUNITY]** Rule draft prefills well (5000¢ / 30d / score 80, Payroll.tsx:320-323) and the default tab shows existing runs first, but run creation starts with two blank dates ("Pick start + end dates", Payroll.tsx:135-151), money is entered in raw CENTS ("Amount (cents)" :387, "Base pay (cents)" :525) while display uses `fmtCents`, and Settings demands a free-typed "Driver ID (Samsara)" (:522). Change: prefill period from last run's end→today; dollar inputs converted to cents on submit; driver dropdown. `Impact: high · Effort: M`
- **[P2 Goal Gradient — N/A]** No multi-step flow; draft→finalized lifecycle shown via status badge (:219-228).
- **[P3 Reciprocity — OPPORTUNITY]** Disabled-module gate says only "Contact your administrator to activate Pay-for-Performance" (:92-95) — asks for effort with zero value preview. Add one concrete-benefit sentence. `Impact: low · Effort: S`
- **[P4 IKEA Effect — APPLIED]** User-named bonus rules are echoed back with human-readable thresholds ("score ≥ 80", :378-411).
- **[P5 Loss Aversion — OPPORTUNITY]** Finalize confirm honestly states irreversibility (:175, good), but "Cancel this run?" (:182) and "Delete rule?" (:362) name neither the object nor the loss — include run period + draft total / rule name + amount. Also `finalize`/`cancel` never check `r.ok` (:174-186), so a failed finalize looks like success. `Impact: med · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Run detail shows Base/Bonus/Total side-by-side with a plain-language breakdown column (:289-302).

### Coaching (assignments / rules)
- **[P1 Smart Defaults — OPPORTUNITY]** Topic auto-defaults to first (Coaching.tsx:157-160), severity defaults to honest middle "medium" (:135), period 7d (:330); but assignment requires free-text driver ID (:194-199) and `onAssign` silently no-ops on missing fields (:164-165). Change: driver select + inline validation message. `Impact: med · Effort: M`
- **[P2 Goal Gradient — N/A]** No staged flow; pending→acknowledged is a filterable status column (:251-260).
- **[P3 Reciprocity — OPPORTUNITY]** Same value-free disabled-module gate as Payroll (:88-90). ("Run engine now", :242-247, is a good give-value-fast affordance once enabled.) `Impact: low · Effort: S`
- **[P4 IKEA Effect — APPLIED]** Rules are user-authored including the driver-facing message (:488), triggers echoed readably (:516-527). Minor gap: Topics tab is read-only; `default_message` not editable.
- **[P5 Loss Aversion — OPPORTUNITY]** Generic confirms "Cancel this coaching assignment?" (:178) and "Delete this rule?" (:393); name the driver/topic and whether pending assignments survive rule deletion. `Impact: low · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Severity enum in honest low→high order with middle pre-selected (:221-225).

### Cost Reports
- **[P1 Smart Defaults — APPLIED]** 90d default with documented quarterly-cadence reasoning (CostReports.tsx:62-94); full value with zero config; proper EmptyState (:283-288).
- **[P2 Goal Gradient — N/A]** Read-only report. **[P3 Reciprocity — N/A]** Nothing asked; analysis, drill-through, CSV all free.
- **[P4 IKEA Effect — OPPORTUNITY]** Period picker resets to 90d every visit (`useState(90)`, :94); persist per-user like DataTable column prefs. `Impact: low · Effort: S`
- **[P5 Loss Aversion — APPLIED]** Exemplary honest loss-framing: `higherIsBad` reds only real spend increases, neutral metrics deliberately info-blue, "no prior period" note instead of a fake 0% (:301-331, :548-573).
- **[P6 Contrast Effect — APPLIED]** Descending-sorted bars with honest "+N more" rollup, top-10 vendor cap, equal-length prior-period anchoring (:41-60, :145-182).

### Maintenance Tasks list
- **[P1 Smart Defaults — APPLIED]** Default "All" = open work only, with honest disclosure of hidden closed rows (Tasks.tsx:719-732, 1866-1877); view mode persisted (:265-273).
- **[P2 Goal Gradient — APPLIED]** Mileage/engine-hours progress columns urgency-sorted (:204-222); chip counts show remaining work (:1725-1758).
- **[P3 Reciprocity — N/A]** Read-only view of the user's own data.
- **[P4 IKEA Effect — APPLIED]** `tableId` persists layout (:1797); bulk checkbox follows the user's pinned column (:1809-1850).
- **[P5 Loss Aversion — APPLIED]** Derived Overdue/Critical grounded in real odometer/date/hours (:627-655) with tooltips disclosing the stored value (:799, :828). Honest urgency, no manufactured scarcity.
- **[P6 Contrast Effect — OPPORTUNITY]** Footer link "N completed/cancelled hidden" (:1871) counts cancelled rows but clicking shows completed only; the `cancelled` bucket (:718) has no chip. Change: add a Cancelled chip or split the link into accurate counts. `Impact: low · Effort: S`

### New Task form (single + multi-vehicle)
- **[P1 Smart Defaults — APPLIED]** Type/priority/trigger defaults (:321-343), template prefill (:498), recurrence seeded from due-period (:1680-1686), worked-example placeholders (:1552).
- **[P2 Goal Gradient — N/A]** Single-screen form; the "Due:" preview is feedback, not a staged flow.
- **[P3 Reciprocity — APPLIED]** Auto-fetches live odometer and shows "Current: X mi" + computed due before asking the user to do arithmetic (:501-513, :1610-1633).
- **[P4 IKEA Effect — APPLIED]** User-authored reusable templates; custom types feed back into pickers.
- **[P5 Loss Aversion — OPPORTUNITY]** No unsaved-changes guard: the header Cancel toggle (:1404-1407) and the `n` shortcut *toggling* `showAdd` (:306) silently wipe a filled form. Change: dirty-check confirm; make `n` open-only. `Impact: med · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Date/Miles/Hours segmented control; priority select is a monotonic low→critical scale.

### Task Edit drawer
- **[P1 Smart Defaults — APPLIED]** Opens to the task's actual trigger dimension (:532-536); absolutes back-converted to "remaining" periods (:563-593).
- **[P2 Goal Gradient — N/A]** Single drawer.
- **[P3 Reciprocity — APPLIED]** Completion evidence (receipt/cost/vendor) requested only at the completion moment (:2240-2335).
- **[P4 IKEA Effect — APPLIED]** Only the viewed trigger is patched; multi-trigger config preserved (:1063-1077); type fixable post-creation (:2067-2076).
- **[P5 Loss Aversion — OPPORTUNITY]** One-click "Mark complete" (:1254-1266, :2371-2381) permanently records DOT attestation with **no disclosure**, while the status-dropdown path (:1143-1150) and bulk path (:876-880) explicitly warn "recorded as the attester… stored permanently." Same permanent legal record, inconsistent honesty — fails the explain-to-their-face test by omission. Change: reuse the same confirm on `handleMarkComplete`. Secondary: the delete Danger Zone (:2441) says "can't be undone" but not what's lost (attachment, recurrence chain). `Impact: high · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Honest 48h/7d/30d snooze ladder shown only when the task is actually nagging (:95-125, :2394-2414); delete is a deliberate two-step inline expansion (:2439-2471).

### Maintenance bulk-action bar
- **[P1 Smart Defaults — APPLIED]** Selection auto-prunes when the visible list changes (:739-747).
- **[P2/P3/P4 — N/A]** Single-click actions; no ask; nothing configurable.
- **[P5 Loss Aversion — OPPORTUNITY]** Bulk complete discloses attestation + recur-spawn (:876-880, exemplary); bulk delete says only "This cannot be undone" (:919-922) without loss scope. Change: mention receipts removed / recurring schedules ending. `Impact: med · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Action order encodes severity: complete (ok) → in progress (info) → delete (destructive, last) (:1896-1926).

### Work Orders list
- **[P1 Smart Defaults — APPLIED]** Empty params default to show-everything; click-through filters render as clearable chips (WorkOrders.tsx:120-130, :205-235).
- **[P2/P3 — N/A]** Read-only list; no ask.
- **[P4 IKEA Effect — APPLIED]** New this changeset: `tableId="work-orders"` persists layout; multi-key search vendor/vehicle/invoice/company (:280-283).
- **[P5 Loss Aversion — APPLIED]** Unpaid card (:255-258) is honest money-at-rest framing grounded in `payment_status`.
- **[P6 Contrast Effect — OPPORTUNITY]** Draft/Unpaid summary cards aren't clickable — the user sees "3 unpaid" then must manually open the column filter; the Maintenance page sets the clickable-chip precedent. Change: cards apply the matching column filter. `Impact: med · Effort: M`

### AI maintenance urgency (chat answers + vehicle chips + snapshot)
- **[P1 Smart Defaults — APPLIED]** Seeded prompt "Any overdue maintenance tasks?" (Chat.tsx:98,105); tool rows pre-ordered overdue→due_soon→pending (ai_tool.py:133-139).
- **[P2/P3/P4 — N/A]** Conversational; no ask; no user configuration.
- **[P5 Loss Aversion — APPLIED]** The manufactured-urgency check passes cleanly: urgency derives from live-merged odometer/hours through the *same* classifier as the dashboard (`classify_task_urgency`, service.py:69-133; fixed 7d/5,000mi/100h thresholds); the tool exposes its evidence (`due_at_miles`, `current_odometer`, `miles_remaining`, ai_tool.py:40-44); derived Critical mirrors the DB's own escalation rule rather than inflating; tests pin honesty in both directions. This change *fixed* a truthfulness bug (AI said "0 overdue" while the page showed a truck past due).
- **[P6 Contrast Effect — APPLIED]** One severity vocabulary across AI and page: overdue=danger / due soon=warn / pending=info, highest-severity-wins dedup (ReferencedVehicles.tsx:23, :98-105).

### Vehicles list
- **[P1 Smart Defaults — OPPORTUNITY]** Code/comment mismatch: the comment (Vehicles.tsx:186-188) says `_city`/`_state` "start `defaultHidden`", but neither column sets `defaultHidden: true` (:115-128) — every persona's first-run table shows Street + City + State, crowding exactly as the comment says it shouldn't. Change: add `defaultHidden: true` to both. Everything else well-defaulted (per-persona column presets :202-211; range-filter steps match display precision :139, :166). `Impact: med · Effort: S`
- **[P2 Goal Gradient — N/A]** Monitoring table; the add/edit wizard lives in VehicleManageDialog (unchanged).
- **[P3 Reciprocity — APPLIED]** Full value (live list, utilization roll-up :325, status-count chips :327-336) before any configuration is asked.
- **[P4 IKEA Effect — APPLIED]** `tableId="vehicles"` (:375) persists the operator's shaped view; Location split with per-piece filters + full-address tooltip (:107-113).
- **[P5 Loss Aversion — N/A]** No destructive action on this surface this changeset.
- **[P6 Contrast Effect — APPLIED]** Honest plainly-labeled filter value sets with explicit "(none)" bucket (:88-92); status chips show real counts (:332-334). Note (not a violation): server-side status chips + client-side Status column filter can combine to a silently empty (but honest) table.

### Storage sync queue
- **[P1 Smart Defaults — APPLIED]** Default filter `'all'` (StorageFileTable.tsx:71); 15s polling with `placeholderData` avoids blank flashes; minimal-table chrome is right for an ops queue (:253-254).
- **[P2 Goal Gradient — APPLIED]** "Retry all stuck (N)" carries live remaining-work count (:158); per-row "Retrying, attempt N" (:276-282).
- **[P3 Reciprocity — APPLIED]** Diagnoses before asking: error-specific chips ("Drive expired/full/Forbidden", :263-274) with raw `last_error` tooltip; the disk-mode empty state explains where to change modes.
- **[P4 IKEA Effect — N/A]** Deliberately a fixed transient outbox (no `tableId`, toolbar off) — coherent choice.
- **[P5 Loss Aversion — APPLIED]** Nothing deletable (only non-destructive retries); copy honest about what lives where ("Synced files live in Drive — they don't show here").
- **[P6 Contrast Effect — APPLIED]** Honest all/pending/stuck segmentation (:135-148); bulk retry only on the stuck view with a real count.

### Settings (working hours / timezone / bot)
- **[P1 Smart Defaults — APPLIED]** Schedule form pre-fills 08:00–17:00 + role driver (Settings.tsx:57-59); TZ picker shows current local time per option (:222-226) turning an abstract choice verifiable; DST tip pre-empts the classic mistake (:204-207).
- **[P2 Goal Gradient — N/A]** Single-step save forms only.
- **[P3 Reciprocity — APPLIED]** Bot section explains what connecting gets you before asking for a token (:352-354); "Looking for your personal preferences?" pointer card (:242-256) respects the user's goal.
- **[P4 IKEA Effect — APPLIED]** Created schedules listed back with label/hours/role.
- **[P5 Loss Aversion — OPPORTUNITY]** `handleDeleteSchedule` (:160-165) fires on a single click of a small "Delete" link (:505-510) — no confirm, no undo, no consequence statement (silently changes when working-hours-gated notifications fire for a whole role). Change: reuse the bot-disconnect inline two-step confirm from the same file (:325-342), naming the schedule and consequence. `Impact: med · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Role dropdown in hierarchy order; plain 00–23 hour selects; nothing pushes the user anywhere.

### Audit Log
- **[P1 Smart Defaults — APPLIED]** Date-range on Time, enum filter reusing the same friendly `ACTION_LABEL`s cells render (AuditLog.tsx:39-46), search widened to action/details/target_id (:111).
- **[P2 — N/A]** Read-only log. **[P3 Reciprocity — APPLIED]** Empty state promises what will appear and from where (:106-108). **[P4 IKEA Effect — APPLIED]** `tableId="audit-log"`. **[P5 — N/A]** Nothing destructive. **[P6 Contrast Effect — APPLIED]** Filter labels match cell labels; no reordering games.

### Navigation (cross-department grants + named briefings)
- **[P1 Smart Defaults — APPLIED]** Grant-implies-nav (generateNav.ts:82-115) fixes a hidden-capability defect — a granted feature is now reachable from the sidebar, not just by URL; own-scope "crumb" flags deliberately do NOT cross departments. Recruiter/HR/accounting get named briefing buttons (useShellConfig.ts:50-60).
- **[P2 — N/A]** Nav, not a flow. **[P3 Reciprocity — APPLIED]** Surfacing already-granted capability is pure value.
- **[P4 IKEA Effect — OPPORTUNITY]** When an owner grants a cross-department permission, the new nav item appears silently — the recipient may never notice. Change: one-session "new" dot on freshly-surfaced cross-grant items (persisted seen-set in user prefs). `Impact: low · Effort: M`
- **[P5 — N/A]** Revoking a grant removing the item is honest. **[P6 Contrast Effect — APPLIED]** Cross-grant items slot into existing groups in catalog order; no promotional reordering.

### Co-owner promotion code email
- **[P1 Smart Defaults — APPLIED]** Copy degrades gracefully with missing data (lifecycle_emails.py:143-144); code is the visual centerpiece (:160-162). Conscious trade noted: OTP in the subject line (:145) is fast but lock-screen-visible — defensible since the code is useless without the authenticated primary-owner session + password.
- **[P2 Goal Gradient — APPLIED]** "Enter this code on the confirmation screen to finish" (:151) marks the last step; stated 15-minute expiry matches the real TTL (router.py:632) — no fabricated countdown.
- **[P3 Reciprocity — APPLIED]** Teaches before it asks: exactly what a co-owner gains and cannot do (:152-155) at the decision moment.
- **[P4 — N/A]** Transactional single-code email.
- **[P5 Loss Aversion — OPPORTUNITY]** Honest as-is (real expiry, proportionate "If you did NOT request this…" :157), but the action *feels* permanent while demotion exists. Add one true line: "You can demote a co-owner later from Team Management." `Impact: low · Effort: S`
- **[P6 Contrast Effect — APPLIED]** Two-way honest framing — "full owner access" AND "does NOT become the primary owner" (:153-155).

### Role-focused AI briefing
- **[P1 Smart Defaults — APPLIED]** Zero-config personalization from *effective* per-account permissions (roles.py:1032-1052), never the role name; "View as ⟨role⟩" persona honored (router.py:516); prompt forbids claiming data "unavailable" for skipped areas (intelligence.py:374-375).
- **[P2 — N/A]** Single-shot generation.
- **[P3 Reciprocity — APPLIED]** Pure value with no ask; recruiters/HR get real application counts by stage injected (router.py:523-543).
- **[P4 IKEA Effect — OPPORTUNITY]** Chat.tsx:404-407 promises the briefing "lives in the conversation and can be followed up on," but `/ai/summary` never calls `save_chat_messages` (router.py:496-556) and the per-turn `sync_history_from_db` hard-replaces model context from the DB (chat.py:28-69) — so a follow-up reaches a model that has never seen the briefing, and the briefing vanishes on reload / never appears in History. Change: persist the briefing turn via `save_chat_messages(..., conversation_id=...)` on the resolved/lazy thread, same as `/chat`. *The face-test failure of this changeset's AI work.* `Impact: med · Effort: M`
- **[P5 Loss Aversion — APPLIED]** The maintenance-snapshot fix (intelligence.py:248-273) eliminates false reassurance — urgency now derived from live readings with the same bucketing as the dashboard chips.
- **[P6 — N/A]** No choices to frame; leading with what most needs attention is honest prioritization.

### AI chat conversational integrity (thread sync + tier re-staging)
- **[P1 Smart Defaults — APPLIED]** No `conversation_id` → latest thread, lazily created (router.py:70-73); explicit tier choices re-staged from DB on cold worker cache (models.py:602-611), fixing a silent dishonesty where a "Reasoning" user was served a Fast model after restart; hand-picked legacy models deliberately not clobbered (:613-620).
- **[P2/P3 — N/A]** No flow; no ask.
- **[P4 IKEA Effect — APPLIED]** Conversational investment now survives across gunicorn workers; context scoped to the on-screen thread so another conversation never leaks in (chat.py:52-54).
- **[P5 Loss Aversion — APPLIED]** Deletion honestly propagates — always-replace sync means a deleted conversation cannot be resurrected by a stale worker (chat.py:41-46, router.py:938-940).
- **[P6 — N/A]** No choice framing.
- Note (outside the 6, one small ticket): new `/conversations*` endpoints surface raw exception class names to end users (`detail=f"Failed to load conversations: {type(e).__name__}"`, router.py:877-880, :898-901, :925-928) — use the file's existing `_safe_error_message` plain-language style. `Impact: low · Effort: S`

### ReferencedVehicles due-soon chips
- **[P1 Smart Defaults — APPLIED]** Automatic classification from tool results, humanized labels, safe fallback ("due soon") when task type absent (ReferencedVehicles.tsx:41-42, :81).
- **[P2/P3/P4 — N/A]** Informational chips; no flow, ask, or user work.
- **[P5 Loss Aversion — APPLIED]** The middle "due soon" tier derives from the same real threshold bucketing the dashboard uses — genuine early warning, not fabricated urgency.
- **[P6 Contrast Effect — APPLIED]** danger/warn/info tones form an honest severity anchor; previously due-soon items were flattened into pending, *understating* real urgency.

### Extension to an already-filed finding (do not double-count)
- The 90-day chat-retention disclosure gap filed in `2026-07-06-datatable-v2-ai-threads.md` should also mention the second silent truncation axis now confirmed in [capabilities/ai/retention.py](../../capabilities/ai/retention.py): a per-user 100-row cap on every write. Suggested combined copy: "Chats are kept for 90 days, up to your most recent 100 messages." `Impact: low · Effort: S`

## Ethics gate
No DARK-PATTERN-RISK findings in this changeset. Three face-test failures by omission (not manipulation), all filed above: (1) undisclosed DOT attestation on one-click Mark complete; (2) briefing "can be followed up on" promise the backend can't honor; (3) Payroll raw-cents inputs where typing "500" meaning $500 creates a $5.00 bonus. Positive examples worth keeping: the AI urgency system (telemetry-grounded, evidence-exposing, dashboard-consistent), Cost Reports' refusal to imply judgment on neutral metrics, the co-owner email's two-way honest framing, and Payroll's `opt_in` default benefiting the driver (acceptable — keep it visible driver-side).

## Top 3 actions (highest impact first)
1. **Disclose DOT attestation on one-click "Mark complete"** (Tasks.tsx:1254-1266, :2371-2381) — reuse the exact confirm the dropdown/bulk paths already show; a permanent legal record must not depend on which button recorded it. `Impact: high · Effort: S`
2. **Fix Payroll money entry + run-date defaults** — dollar inputs converted to cents on submit, period prefilled from last run's end→today, driver dropdown instead of free-typed Samsara ID; also check `r.ok` on finalize/cancel so failures don't look like success. `Impact: high · Effort: M`
3. **Persist the AI briefing turn to the conversation** (`/ai/summary` → `save_chat_messages`) so the promised follow-up actually works and the briefing survives reload/History. `Impact: med · Effort: M`

Runners-up (all med/S): two-step confirm on Working Hours schedule delete (Settings.tsx:160-165); `defaultHidden: true` on Vehicles `_city`/`_state` (comment/code mismatch, Vehicles.tsx:115-128); unsaved-changes guard + `n` open-only on the New Task form (Tasks.tsx:306, :1404-1407); loss scope on maintenance bulk delete (Tasks.tsx:919-922).

## NEEDS-CONTEXT items
- None — every principle was judgeable from in-repo working-tree code.
