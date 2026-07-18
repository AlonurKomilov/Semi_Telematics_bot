# AI Copilot — Phase 4: Write Actions ("Hands")

**Status:** design for review · **Author:** session 2026-07-12 · **Prereq:** Phases 1–3 landed
**Risk class:** HIGH — this is the first time the AI *mutates* tenant data (tasks, acknowledgements, later payroll/invites). It gets a dedicated design + a security review + fable-advisor sign-off on the confirmation contract **before any code**, per the repo's CLAUDE.md escalation rules.

---

## 0. The one-sentence shape

> The AI never writes directly. A write **tool** returns a **proposal**; the
> user sees an **approve card**; on Approve a **separate endpoint executes**,
> re-checking permission + scope server-side and enforcing single-use via an
> idempotency key.

Every noun above is a registration or an existing mechanism reused. No new
central switch; a new write action is a `@register_tool(..., writes=True)` in
its own `features/x/ai_tool.py` plus a card renderer in `features/x/ai_actions/`.

---

## 1. Threat model (what could go wrong, and the control)

| Threat | Control |
|---|---|
| **Prompt injection makes the AI delete/modify data** | The AI can only *propose*. Nothing mutates without an explicit human Approve click. A malicious prompt can at most surface a proposal the user will see and can reject. |
| **User approves, but lacks permission for that write** | The **execute endpoint re-runs `_check_tool_permission` + scope** at execution time. The card is UX; the server is the gate. Approving never bypasses authz. |
| **Cross-tenant / cross-company write** | Execute endpoint resolves `account_id` from the JWT (never the proposal), and applies the same company/vehicle scope the read tools already enforce. |
| **Double-execution (double-click, ret/replay)** | Each proposal carries a server-minted **idempotency key**; the execute endpoint burns it on first use. A second submit is a no-op returning the first result. |
| **Stale/tampered proposal** (client edits amounts before approving) | The proposal is **server-signed / server-stored**: the client approves by key, not by resending a mutable body. The server re-derives the action from what it stored, not from the client. |
| **Confused-deputy: AI proposes an action the user didn't ask for** | The card shows the **exact effect in plain language** ("Create oil-change task for Truck 228, due 2026-08-01") before Approve. Nothing implicit. |
| **Silent escalation over time** | Every executed write is written to the existing **`audit_log`** (account-scoped) with actor = the user, source = "ai_copilot", plus the proposal id. Fully attributable. |
| **Destructive irreversibility** | Phase 4.0 ships only **create / low-risk** actions (create task, acknowledge alert). Delete / money-moving / bulk actions are gated behind a higher `risk` tier and deferred to 4.1 with per-action review. |

The load-bearing invariant: **the confirmation UI is a convenience, not a
security boundary.** If the card were bypassed entirely (scripted approve),
the execute endpoint alone must still be safe — permission, scope, tenancy,
idempotency all enforced server-side.

---

## 2. Backend contract

### 2A. Declaring a write tool (registration, in the feature's own file)

```python
# features/maintenance/ai_tool.py
@register_tool({
  "name": "create_maintenance_task",
  "description": "Create a maintenance task for a vehicle.",
  "parameters": { … JSON schema … },
  # NEW — marks this as a write tool:
  "writes": True,
  "risk": "low",                       # low | medium | high (gates what's allowed)
  "confirm": {                          # how to summarize for the approve card
    "title": "Create maintenance task",
    "artifact_type": "confirm_create_task",   # → frontend card renderer
  },
})
async def create_maintenance_task(tool_args, samsara_client, account_id=None, db=None):
    # In PROPOSE mode this NEVER mutates — it validates + returns a proposal.
    # The same function runs in EXECUTE mode (a flag) to actually write.
    ...
```

Two registry additions, both optional and backward-compatible: `writes`,
`risk`, `confirm`. Read tools are unchanged.

### 2B. Propose vs execute — one handler, two modes

A write tool's handler is called in **propose mode** during a normal chat
turn. It must:
1. Validate args (vehicle exists, date sane, in scope).
2. **Not mutate.**
3. Return a proposal envelope via a new helper:

```python
return tool_propose(
    action="create_maintenance_task",
    summary="Create a Full PM (oil) task for Truck 228, due 2026-08-01.",
    payload={"vehicle_name": "228", "task_type": "oil", "due_date": "2026-08-01"},
    risk="low",
    artifact_type="confirm_create_task",
)
```

`tool_propose` (in `capabilities/ai/tools/registry.py`):
- Mints a `proposal_id` (uuid) + `idempotency_key`.
- **Persists** the proposal server-side (short-TTL table `ai_action_proposals`:
  `id, account_id, user_id, tool, payload_json (encrypted), risk, status, created_at, expires_at`)
  so the client can only approve by id — it can't tamper with the payload.
- Returns a lightweight artifact `{type: "action_proposal", proposal_id, summary, risk, artifact_type}`
  that flows to the client on the `done` event (reusing the Phase-3 artifact channel).

The AI's text answer explains what it's about to do; the card carries the button.

### 2C. The execute endpoint (the real gate)

```
POST /ai/actions/{proposal_id}/approve
```
- `Depends(get_current_user)` — authed.
- Loads the proposal; **404 if not this (account, user)** — proposals are
  per-user, non-enumerable (uuid).
- **Expired / already-consumed → 409** (idempotent: return the stored result).
- **Re-runs `_check_tool_permission(tool, payload, role, user_context, account_id)`**
  — the identical gate the read path uses. Blocked → 403.
- Re-applies company/vehicle scope to the payload's target.
- Calls the tool handler in **execute mode** → performs the write.
- Burns the idempotency key; stores the result on the proposal row.
- Writes `audit_log(account_id, user_id, action="ai_write:create_maintenance_task",
  target_type, target_id, details=payload+proposal_id)`.
- Returns the result (the created task, etc.) for the card to show "Done".

`POST /ai/actions/{id}/reject` just marks the proposal declined (no write) —
so the audit trail records that the user saw and refused it.

---

## 3. Frontend contract

### 3A. Approve card = an artifact type (reuses Phase 3)

The proposal arrives as an `action_proposal` artifact on the message. It
renders through the **artifact registry** — so the card is just another
registered renderer, and a feature ships its own card in
`features/x/ai_actions/`:

```tsx
// features/maintenance/ai_actions/ConfirmCreateTask.tsx
registerArtifact('confirm_create_task', (a) => <ConfirmCreateTaskCard proposal={a} />);
```

The card shows the summary + Approve / Reject. Approve → `POST
/ai/actions/{id}/approve`; on success it flips to a "Created ✓ — Open task"
state (reusing the Phase-2 deep-link). Reject → `/reject`.

Generic fallback: an `action_proposal` with no feature-specific card renders
a **default confirm card** (summary + risk badge + Approve/Reject) so a new
write tool works before its bespoke card exists.

### 3B. State

- Cards are **not** browser-local like thought logs — a proposal is
  server-authoritative (it must survive to be approved from any device and be
  audited). The proposal row IS the record; the card reads `GET
  /ai/actions/{id}` for current status on load, so a refreshed page shows
  "already approved" correctly.
- One-proposal-per-message; approving disables the card (optimistic) then
  confirms from the server.

---

## 4. What ships in 4.0 vs deferred

**4.0 (this phase):** the whole machinery + exactly **two low-risk actions** as
proof — `create_maintenance_task` and `acknowledge_alert`. Both are additive
(create / state-flip), both easily reversible by the user, both already have
permission flags (`can_maintenance_all`, `can_alerts_*`).

**4.1+ (separate reviews, each):** edits, deletes, bulk actions, and anything
touching **money or identity** (payroll runs, invites, role changes, Stripe).
These require `risk: "high"`, a stricter confirm (typed confirmation or 2FA
for the money ones — mirroring the co-owner-promotion 2FA pattern), and their
own security pass. The `risk` field exists from day one precisely so the
execute endpoint can refuse a `high` action until that path is built:

```python
if proposal.risk == "high" and not HIGH_RISK_WRITES_ENABLED:
    raise HTTPException(403, "This action type isn't enabled yet.")
```

---

## 5. Files (feature-centric, registration-only)

```
capabilities/ai/tools/registry.py     EDIT: tool_propose() helper; writes/risk/confirm in schema
capabilities/ai/router.py             EDIT: POST /ai/actions/{id}/approve | /reject | GET status
capabilities/ai/actions.py            NEW: propose/execute orchestration + the re-auth gate
adapters/storage/ai_actions.py        NEW: ai_action_proposals table CRUD (encrypted payload, TTL)
adapters/storage/platform_schema.py   EDIT: ai_action_proposals table
adapters/storage/platform_migrations.py EDIT: create table (idempotent)
features/maintenance/ai_tool.py       EDIT: create_maintenance_task (propose+execute modes)
capabilities/alerting/ai_tool.py      EDIT: acknowledge_alert (propose+execute modes)

interfaces/dashboard/src/features/ai/artifacts/            (registry already exists)
  ActionProposalCard.tsx              NEW: generic fallback confirm card (registers 'action_proposal')
interfaces/dashboard/src/features/maintenance/ai_actions/  NEW folder
  ConfirmCreateTask.tsx               NEW: bespoke card
interfaces/dashboard/src/features/alerts/ai_actions/
  ConfirmAcknowledge.tsx              NEW: bespoke card
interfaces/dashboard/src/api/client.ts  EDIT: approveAction / rejectAction / getActionStatus
```

**Unchanged:** the whole read path, tool loop, streaming, threads, artifacts
renderer, permission masking. Write actions are an additive layer.

---

## 6. Future-proofing checklist

| Add a… | You touch | You do NOT touch |
|---|---|---|
| new **write action** | its `features/x/ai_tool.py` (`writes=True` + propose/execute) + `features/x/ai_actions/<Card>` | the approve/execute endpoint, the gate, the audit wiring |
| new **confirm card style** | that feature's `ai_actions/` renderer | the artifact registry, other cards |
| **higher-risk action** | set `risk` + flip its enable flag after its own review | the low-risk path |

The execute endpoint, the re-auth gate, the idempotency + audit are **written
once** and shared by every action. A new action never edits them.

---

## 7. Open questions for you (answer before build)

1. **Scope of 4.0 actions** — confirm the two proofs (create task + acknowledge
   alert), or swap one for something you'd actually use first?
2. **Proposal persistence** — I propose a short-TTL server table (survives
   device switch, auditable). OK, or do you want proposals ephemeral
   (in-memory, expire on turn end — simpler, but can't approve from another
   device)?
3. **Confirmation friction** — for `low` risk: single Approve click. Agree?
   And for the eventual money/identity actions, confirm we reuse the existing
   2FA (password + emailed code) pattern from co-owner promotion.
4. **Auto-apply ceiling** — should there EVER be a "don't ask me again for
   low-risk creates" preference, or is every write always confirmed? (I lean
   always-confirm for launch; revisit later.)

---

## 8. Recommended sequencing

1. Schema + `tool_propose` + the execute endpoint + the re-auth gate + audit
   (the shared spine) — with `create_maintenance_task` as the single proof.
2. The generic `action_proposal` card + the bespoke maintenance card.
3. Add `acknowledge_alert` as the second action (proves the registry — should
   be ~an hour once the spine exists).
4. Security review of the spine (the gate + idempotency + tenancy) before
   enabling in production.

Nothing here is built yet — this is the design to approve/adjust first.

---

## 9. Shipped — as-built notes (post code-review)

Spine + both actions (`create_maintenance_task`, `acknowledge_alerts`) +
generic `action_proposal` card shipped. Code-review + a fable-advisor
security consult drove three hardening changes beyond the original design:

- **Vehicle-scope on write tools is now a declared, test-enforced contract.**
  Every `writes:True` tool must declare `scope ∈ {vehicle_param, resource_ids,
  account_unscoped}` matching its frozenset membership
  (`tests/test_ai_write_tool_scope.py`), and `_check_tool_permission` **fails
  closed** for any unclassified write by a scoped caller. `acknowledge_alerts`
  (resource_ids) enforces scope authoritatively in the storage SQL —
  `acknowledge_alert_history(allowed_vehicle_names=…)` filters the clear to the
  approver's vehicles in the same statement pair (TOCTOU-free); the propose
  step + gate are cheaper earlier backstops. `create_maintenance_task`
  (vehicle_param) is scope-checked at the gate.
- **Attribution** uses the real approving user id (JWT subject), injected into
  the executor context — never the `0` "auto-resolved" sentinel.
- **Surface gating** — writes are suppressed on the non-streaming `/ai/chat`
  path (Telegram miniapp) which has no approve UI / no proposal persistence.

### Known follow-ups (non-blocking)
- **Mixed-batch existence oracle** (minor): in an `acknowledge_alerts` batch, a
  scoped caller can distinguish a *known-but-out-of-scope* id (refused at
  propose) from a *foreign/nonexistent* id (silently no-ops at execute). It
  cannot clear anything out of scope — purely an existence signal. Tighten by
  also flagging ids absent from `get_alert_history_vehicles` for scoped callers
  if we ever want to close the oracle.

## Undo (shipped 2026-07-18 — U1 494df38 · U2 e20a04f · U3 e83e422)

Copilot-style **change-set undo**, never a point-in-time restore (which
would rewind other users' concurrent work): an executed action may be
reversed to exactly what IT created, and nothing else.

- **Contract:** an action type MAY register its reverse recipe
  (`register_undo_executor(name)` — signature
  `(result, payload, account_id, user_context, db) → dict`).  The code
  registry is the trust root, mirroring executors.  No recipe → no Undo
  (high-risk/destructive actions remain disabled entirely).
- **Manifest:** the executor records its change-set in the stored result
  under underscore-prefixed keys (`_item_ids`) — server-side by
  convention: `_client_result()` strips them from every client response,
  and `finalize_action_proposal` no longer truncates `result` (a
  shortened manifest would corrupt the reversal).
- **Authorization ladder:** the APPROVER may undo their own action;
  owner/admin may undo any employee's (the "found it in Audit Log →
  fix it" path).  Both re-checked against the tool's own permission on
  the real JWT role; persona preview never honored.
- **State machine:** `consumed → undoing → undone` via an atomic
  conditional claim (no double-undo); recipe failure reverts to
  `consumed` so the undo stays available.  `undone_at`/`undone_by`
  columns record who/when; the undo outcome is merged into the stored
  result (`_undo`) so a refreshed card shows it.
- **Window:** 7 days (`UNDO_WINDOW_DAYS`), matching the proposal row's
  retention sweep and enforced explicitly.
- **Recipes must be soft + evented + tolerant:** the inventory recipe
  soft-removes (`is_active=0` + an "AI import undone" event per item —
  the trail survives; even the undo is recoverable) in ONE transaction,
  skips items already removed manually, and fails closed (409, claim
  rolled back) on a legacy result without a manifest.
- **Audit:** `ai_undo:<tool>` with the undoer as actor + who originally
  approved; the Audit Log page labels AI rows ("AI: …") so the Action
  filter doubles as an "AI actions only" view.
- **UI:** the executed card shows a ghost Undo (server-driven
  availability) with a two-click warn-toned confirm that self-disarms;
  `undoing`/`undone` phases reconcile from `GET /ai/actions/{id}` like
  every other phase.

Adding undo to a future action = register one recipe + record the
change-set in its result.  Tests: `tests/test_ai_action_undo.py`.
