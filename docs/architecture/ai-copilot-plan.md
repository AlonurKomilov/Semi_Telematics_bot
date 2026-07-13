# AI Assistant → Copilot: Architecture Plan

**Status:** proposal for review (rev. 2) · **Author:** session 2026-07-12 · **Scope:** universal, feature-centric, zero per-feature/per-role hardcoding

> **rev. 2 — naming corrections after review:**
> 1. No `copilot/` sub-namespace. "Copilot" is a product name, not an
>    architectural boundary — it is the SAME AI assistant, embedded. All
>    new frontend code lives directly under the existing `features/ai/`
>    home (the frontend is flat `features/` — it has no `capabilities/`
>    folder; AI is a capability on the BACKEND at `capabilities/ai/`, and
>    the frontend's one AI home is `features/ai/`). We do not create a
>    parallel or nested "copilot" concept.
> 2. Per-feature AI folders carry the **`ai_` prefix** to match the
>    existing `features/x/ai_tool.py` convention: `ai_artifacts/`,
>    `ai_actions/` — never bare `artifacts/`/`actions/` (ambiguous).

---

## 0. The one rule this plan is built around

> Adding a new artifact type, a new tool, a new deep-link target, or a new
> confirmable write action must be a **registration**, never a **refactor**.

Every mechanism below is a **registry + a contract**. A feature contributes
by adding an entry (backend `@register_*`, frontend a catalog row or a
`registerX()` call) in **its own folder**. No central switch statement, no
`if feature === 'maintenance'` anywhere, no edits to shared components when
feature #37 arrives. This is the same philosophy the codebase already uses
for `featureCatalog.ts` (nav), the AI `@register_tool` registry, and the
retention-hub `register_target`/`register_need` contributors — we extend the
pattern, we don't invent a new one.

**How to judge any future PR against this plan:** if adding a capability
touches a file outside the contributing feature's folder (other than adding
one line to a registry index), the design has leaked. Flag it.

---

## 1. Where we are vs. where we're going

| | Assistant (today) | Copilot (target) |
|---|---|---|
| Location | separate page `/ai/chat` | slide-over panel on **every** page + the full page |
| Page awareness | none (global snapshot) | knows the feature, filters, selection you're viewing |
| Attach context | none | `@mention` an entity, "use what's on screen" |
| Steps | text labels | labels **+ deep-link buttons** into the product |
| Output | prose (HTML) | prose **+ artifacts** (tables, charts) rendered natively |
| Actions | read-only Q&A | **proposes writes**, you approve, server executes |
| Engine | ✅ tools, tiers, threads, streaming, permission-scoping, encryption | (unchanged — already copilot-grade) |

**The engine is done.** This plan is entirely about **presence, context,
hands, and output** — and every piece is a registry.

---

## 2. The registries (the whole architecture in one table)

| # | Capability | Registry / contract | A feature contributes by… | Lives in |
|---|---|---|---|---|
| A | Assistant panel | — (single shell mount) | nothing — it's global | `shells/` + existing `features/ai/` |
| B | Page context | `PageContextProvider` + `usePublishContext()` hook | calling `usePublishContext({...})` in its page | each feature's page file |
| C | Deep links | `tool → route` map **derived from `featureCatalog`** | already declaring its catalog entry + tool | `featureCatalog.ts` (existing) + tiny resolver |
| D | Artifacts | `artifactRegistry` (type → React renderer) | registering a renderer **only if** it needs a NEW viz type | `features/ai/artifacts/` |
| E | Write actions | backend `@register_tool(..., writes=True, confirm=...)` + frontend `actionCardRegistry` | registering a write-tool in its own `ai_tool.py` | each feature's `ai_tool.py` |
| F | Durations / bg tasks | event timestamps (already emitted) | nothing — universal | `intelligence.py` (existing stream) |

Read the rest of the doc as: **one section per registry**, each showing the
contract, the files, and "how feature #37 uses it."

---

## 3. Phase 1 — Copilot panel + page context

### 3A. The panel (presence)

**What:** the chat becomes a right-docked slide-over available on any page,
opened from a topbar button and a hotkey (⌘J), conversation persisting
across navigation. `/ai/chat` stays as the full-page view of the **same**
threads (same endpoints, same `thoughtStore`, same everything).

**Why it's not a rewrite:** the `Chat.tsx` we built this week already IS the
panel body. We extract its render into a `<ChatBody>` used by both the
page and the panel; the only new code is the slide-over frame + open/close
state, mounted once in the shell exactly like `CommandPalette` already is
(`shells/DefaultShell.tsx:147`).

**Files** (all under the existing `features/ai/` — no new "copilot" folder):
```
NEW  src/features/ai/AssistantPanel.tsx        slide-over frame (open state, ⌘J, focus trap)
NEW  src/features/ai/AssistantContext.tsx      open/close + "prefill this question" bus
EDIT src/features/ai/Chat.tsx                  extract body → <ChatBody>, page wraps it
NEW  src/features/ai/ChatBody.tsx              the shared chat body (moved, not rewritten)
EDIT src/shells/DefaultShell.tsx (+5 sibling shells) mount <AssistantPanel/> once, add topbar button
EDIT src/components/shell/KeyboardShortcuts.tsx    ⌘J → open the panel
```
Feature-centric: **untouched.** No feature folder changes for the panel.

### 3B. Page context (the copilot sees your screen)

**The contract** — one provider, one hook, one descriptor shape:

```ts
// src/features/ai/PageContext.tsx
export interface PageContext {
  feature: string;                    // 'maintenance' — MUST equal a featureCatalog id
  label: string;                      // 'Maintenance' — human, for the "@ Maintenance" chip
  filters?: Record<string, unknown>;  // whatever the page's filter state is
  selectedIds?: (string | number)[];  // rows the user selected
  focus?: { kind: string; id: string; label: string }; // "the truck 228 you opened"
}
export function usePublishContext(ctx: PageContext | null): void;  // publishes while mounted, clears on unmount
export function useCurrentPageContext(): PageContext | null;        // panel reads this
```

**How feature #37 uses it — this is the entire integration for a page:**
```tsx
// features/maintenance/Tasks.tsx  (or any feature page, present or future)
usePublishContext({
  feature: 'maintenance',
  label: t('nav.maintenance'),
  filters: { status: statusFilter, company: companyFilter },
  selectedIds: selectedRows,
  focus: openTask ? { kind: 'task', id: openTask.id, label: `Truck ${openTask.vehicle}` } : undefined,
});
```
One hook call in the page it belongs to. Nothing else. A recruiter's
Applications page publishes `{feature:'applications', ...}` and — because
tools + snapshot are **already permission-masked per role** — the copilot
becomes a hiring copilot with no new code.

**Backend:** `/ai/chat` + `/ai/chat/stream` accept an optional
`page_context` field (like they already accept `conversation_id`).
`build_context` folds it into the prompt as "The user is currently viewing:
…". **No tool changes** — it's a prompt-context enrichment, exactly how
persona-preview already works. One edit to the request model + one to the
prompt builder.

**Files:**
```
NEW  src/features/ai/PageContext.tsx                provider + hooks (~60 lines)
EDIT each feature page (opt-in, incremental)        one usePublishContext() call
EDIT src/api/client.ts                              add page_context to stream body
EDIT capabilities/ai/router.py                      ChatRequest.page_context field
EDIT capabilities/ai/intelligence.py                fold context into build_context prompt
```

**Guardrail:** `page_context` is a *hint for the prompt*, never an
authorization input. The backend still resolves tools + vehicle scope from
the JWT — a spoofed `page_context` can change what the model is *told the
user is looking at*, but not what data it's *allowed to fetch*. This is the
same trust boundary as `X-View-As` persona preview.

---

## 4. Phase 2 — Deep links + per-step durations

### 4C. Deep links (steps link back into the product)

**No new registry** — derive the map from `featureCatalog.ts`, which
already pairs a feature `id` with its `path`. Tools are named after their
feature (`get_maintenance_summary` → `maintenance`), so:

```ts
// src/features/ai/toolLinks.ts  — the WHOLE resolver
import { CATALOG } from '../../../config/featureCatalog';
const byId = new Map(CATALOG.map(f => [f.id, f]));
// tool name → feature id: explicit overrides for the few that don't match by prefix
const TOOL_FEATURE: Record<string,string> = { get_alert_history: 'alerts', /* … */ };
export function toolDeepLink(toolName: string) {
  const featureId = TOOL_FEATURE[toolName] ?? toolName.replace(/^get_/, '').split('_')[0];
  const f = byId.get(featureId);
  return f ? { path: f.path, label: f.labelKey } : null;   // → "Open Maintenance" button
}
```
Feature #37 adds a tool + its catalog row (which it does anyway for nav) →
its steps get an "Open X" button **for free**. Entity-level links (a vehicle
mention → Live Map with that truck focused) reuse the existing
`ReferencedVehicles` chip mechanism, generalized to an `entityLinks` map
(`vehicle → /live-map?focus=`, `driver → /drivers?focus=`) — one small
lookup table, extended per entity kind, not per feature.

**Files:**
```
NEW  src/features/ai/toolLinks.ts             resolver (~30 lines, reads catalog)
NEW  src/features/ai/entityLinks.ts           entity-kind → route (small table)
EDIT src/features/ai/ChatBody.tsx (timeline)  render the button when a link resolves
```

### 4F. Durations (cheap, universal)

Per-step timing already exists implicitly — the stream wrapper timestamps
every event. Emit `elapsed_ms` on each tool step's completion; the timeline
renders "12s / 8s / 4s" per card like the example. **One edit** to the
`_finish_process` assembly in `intelligence.py`; the frontend timeline reads
a field that's now populated. No registry — it's intrinsic.

---

## 5. Phase 3 — Artifacts (the "Generating Table" moment)

### 5D. The artifact registry

**The contract** — a discriminated union + a renderer map:

```ts
// src/features/ai/artifacts/types.ts
export interface Artifact { type: string; title?: string; [k: string]: unknown; }
export interface TableArtifact extends Artifact { type:'table'; columns:{key,label}[]; rows:Record<string,unknown>[]; }
export interface ChartArtifact extends Artifact { type:'chart'; chart:'bar'|'line'; series:…; }

// src/features/ai/artifacts/registry.ts
type Renderer = (a: Artifact) => React.ReactNode;
const RENDERERS: Record<string, Renderer> = {};
export function registerArtifact(type: string, render: Renderer): void;
export function renderArtifact(a: Artifact): React.ReactNode;  // falls back to <pre> for unknown types
```

**Built-in renderers register themselves** (table → your existing
`DataGrid`; chart → your existing `recharts` + `chartColor()` tokens), so
tables and charts work on day one and obey the design system.

**How feature #37 adds a NEW artifact type** (say, a route map preview) —
this is the entire contribution, in its own folder, `ai_`-prefixed to match
the `ai_tool.py` convention so it reads unambiguously as "the AI part of
this feature":
```tsx
// features/routes/ai_artifacts/RouteMapArtifact.tsx
registerArtifact('route_map', (a) => <RouteMiniMap stops={a.stops} />);
```
Register it in the artifacts barrel (one line, same as adding a retention
contributor). **No shared file changes. No refactor.** Unknown artifact
types degrade to a readable fallback instead of crashing — so an old client
meeting a new artifact type never breaks.

**Backend:** the model already produces structured tool results — today we
flatten them to prose and discard the structure. Instead, tools optionally
return an `artifacts: [...]` list in their result envelope (additive — tools
that don't, behave exactly as now), and the `done` event carries them.
Artifacts are **display-only, browser-local** like the process timeline
(same privacy posture we just established — the DB stores text, not
rebuildable UI payloads).

**Files:**
```
NEW  src/features/ai/artifacts/types.ts registry.ts index.ts   the contract + barrel
NEW  src/features/ai/artifacts/TableArtifact.tsx ChartArtifact.tsx   built-ins (use DataGrid/recharts)
EDIT src/features/ai/.../message bubble                         renderArtifact(a) under the answer
EDIT capabilities/ai/tools/registry.py (envelope helper)       optional artifacts[] in tool_ok()
EDIT capabilities/ai/intelligence.py                           carry artifacts on done event
```
Per-feature artifact renderers live in `features/<x>/ai_artifacts/` (the
`ai_` prefix, same as `ai_tool.py`) — the feature-centric rule holds. The
SHARED registry + built-ins live under `features/ai/artifacts/`, which needs
no prefix because it's already inside the AI home (everything there is AI).

---

## 6. Phase 4 — Hands (write actions) · designed, not built yet

The genuine "copilot" leap, and the one with real security surface — so it
gets its **own design pass** before code. The shape:

**Backend contract** (extends the existing tool registry, additive):
```python
@register_tool({... "writes": True,
  "confirm": {"summary": "Create oil-change task for Truck 228",
              "risk": "low"}})
async def create_maintenance_task(args, ...):   # lives in features/maintenance/ai_tool.py
```
Write-tools **propose** — the agent returns a `proposed_action` instead of
executing. The frontend renders it as an **approve card** (your Datatruck
plan→apply philosophy exactly). On Approve, a *separate* endpoint executes,
**re-checking `_check_tool_permission` server-side** — the confirmation UI
is convenience, the server is the gate. Idempotency key per proposal so a
double-click can't double-write.

**Frontend:** an `actionCardRegistry` (like artifacts) maps action type →
a confirm-card renderer, so feature #37's write action gets a card by
registering one. Feature-centric: cards in `features/<x>/ai_actions/`.

Because this touches money/PII/tenant-writes, I'd bring the **security
review + fable-advisor** in on the confirmation contract before building
(per the repo's CLAUDE.md escalation rules).

---

## 7. Folder map (everything new, feature-centric)

FRONTEND — the flat `features/` layout has NO `capabilities/` folder; AI's
one frontend home is `features/ai/`, and everything new goes there directly
(no `copilot/` nesting — "copilot" is a product name, not a folder):
```
interfaces/dashboard/src/
  features/ai/                   ← the EXISTING AI home; all new plumbing lands here
    Chat.tsx                     (EDIT: thin wrapper over ChatBody)
    ChatBody.tsx                 ← NEW: shared chat body (extracted, used by page + panel)
    AssistantPanel.tsx           ← NEW: slide-over frame  (registry A)
    AssistantContext.tsx         ← NEW: open/close + prefill bus
    PageContext.tsx              ← NEW: publish/consume page context  (registry B)
    toolLinks.ts                 ← NEW: tool→route, derived from catalog  (registry C)
    entityLinks.ts               ← NEW: entity→route table  (registry C)
    artifacts/                   ← NEW: artifact registry  (registry D)
      types.ts registry.ts index.ts
      TableArtifact.tsx ChartArtifact.tsx      built-ins (DataGrid / recharts)
    sections/ thoughtStore.ts …  (existing, unchanged)
  features/<any-feature>/
    <Page>.tsx                   (EDIT, opt-in: one usePublishContext call)
    ai_artifacts/                (OPTIONAL: feature-specific artifact renderers — ai_ prefix)
    ai_actions/                  (Phase 4: feature-specific approve cards — ai_ prefix)

BACKEND — AI stays the capability it already is (capabilities/ai/); per-
feature AI integration stays in each feature's ai_tool.py:
capabilities/ai/
  router.py                      (EDIT: page_context field; Phase 4: /approve endpoint)
  intelligence.py                (EDIT: fold context; durations; carry artifacts)
  tools/registry.py              (EDIT: optional artifacts[] + writes/confirm in schema)
features/<x>/ai_tool.py          (per-feature: existing read tools; Phase 4: write tools)
```

**What does NOT change:** the tool execution loop, permission masking,
vehicle-scoping, tier routing, streaming bridge, thread history, encryption,
`thoughtStore`. All of this week's work carries forward untouched.

---

## 8. Future-proofing checklist (how each "add a thing" stays a registration)

| Add a… | You touch | You do NOT touch |
|---|---|---|
| new **read tool** | `features/x/ai_tool.py` (`@register_tool`) | anything shared (already true today) |
| new **page's context** | that page (`usePublishContext`) | the panel, the backend prompt code |
| new **deep-link target** | its `featureCatalog` row (already exists for nav) | the resolver |
| new **artifact type** | `features/x/ai_artifacts/` + one barrel line | shared renderers, the message bubble |
| new **write action** | `features/x/ai_tool.py` + `features/x/ai_actions/` | the approve/execute plumbing |
| new **role/persona** | (already covered by the existing role playbook) | the copilot — context+tools are permission-derived |

If a future change can't be expressed as a row/registration in the left
column, that's the signal the abstraction needs revisiting **before**
merging — not a reason to hardcode.

---

## 9. Recommended sequence & sizing

1. **Phase 1 (panel + page context)** — ~2–3 focused days. Biggest felt
   change; pure build on this week's work; no backend risk beyond a prompt
   field. **Start here.**
2. **Phase 2 (deep links + durations)** — ~1 day. Small, high polish.
3. **Phase 3 (artifacts)** — ~2–3 days. Visible "wow"; the registry is the
   careful part, the built-in table/chart are straightforward.
4. **Phase 4 (write actions)** — own project + security pass. Not before
   1–3 prove the panel is where users work.

Each phase ships independently and leaves the product fully working.

---

## 10. Open questions for you

1. **Panel default side & width** — right-docked overlay (like the example)
   or a push-content split? Overlay is less disruptive to existing layouts.
2. **Phase 3 artifacts privacy** — confirm browser-local (matches the
   thought-log decision) vs. persisted. Local keeps the DB lean; persisted
   survives device switches. I lean local.
3. **Phase 4 appetite** — do you want write actions on the near roadmap
   (changes how carefully we design the tool envelope now) or purely
   read-copilot for the foreseeable future?
