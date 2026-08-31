# AI Import Assistant — spreadsheet → Inventory through the copilot

*Design doc (pre-implementation). Drafted 2026-07-16. Companion to
[ai-copilot-phase4-write-actions.md](ai-copilot-phase4-write-actions.md);
its other companion, the July copilot plan, was deleted 2026-08-31 as
shipped-and-superseded (owner call — git history keeps it).*

## 1. What this is

Fleets keep operational reality in spreadsheets. The real source driving
this design is a Google-Sheets **matrix**: rows = vehicle units (`22`,
`96`, `103 OSY`, `103 G1`), columns = onboard item types (`Fire
extinguisher`, `Emergency Triangle`), cells = statuses (`Good` / `Not
checked` / `3 of 3`), plus free-text notes in mixed Uzbek/Russian/English
("remontda bolshi kere", "Driver kasal ekan").

A classic template-CSV importer rejects that file. The copilot doesn't:

> Attach the file you actually have → the AI normalizes it → you see
> exactly what will be created → one Approve writes it to Inventory.

This is deliberately built ON the existing copilot spine — every step is
a **registration into machinery that already exists**, not new
architecture:

| Step | Existing machinery |
|---|---|
| Attach | the composer `+` menu (built as "the future attach/upload home") |
| Preview | table artifacts (DataGrid-rendered in chat) |
| Confirm | propose → approve → execute write actions (encrypted proposals, atomic claim, real-JWT re-auth) |
| Write | `add_inventory_item` storage (actor attribution + audit log) |
| Guardrails | write-tool scope contract (test-enforced), `can_manage_vehicles` |

## 2. How it looks (UX)

```
┌ composer ────────────────────────────────────────────┐
│ Import this checklist into inventory                  │
│ 📎 inventory-checklist.csv · 32 rows                  │  ← attachment chip
│ ⊞                    [⚡ Reasoning ⌄]  (↑)             │
└────────────────────────────────────────────────────────┘

assistant (flat, streaming):
● Reasoning ⌄
✓ Read attachment · 1s
✓ Propose inventory import · 2s

  I mapped your sheet: "Units" → vehicle, and two item columns
  ("Fire extinguisher", "Emergency Triangle" → safety items with
  statuses). 58 items across 29 vehicles will be created.

  ┌ TABLE ARTIFACT (first 15 rows + totals) ───────────────┐
  │ Vehicle │ Item                │ Status      │ Note      │
  │ 22      │ Fire extinguisher   │ good        │           │
  │ 22      │ Emergency Triangle  │ installed   │ 3 of 3    │
  │ 96      │ Fire extinguisher   │ missing     │ remontda… │
  │ …                                                       │
  └─────────────────────────────────────────────────────────┘
  ⚠ Skipped (no registry match): 2303 · 96?  — never guessed.

  ┌ ACTION CARD ────────────────────────────────────────────┐
  │ Import 58 inventory items across 29 vehicles            │
  │ Creates records with you as the actor — each item can   │
  │ be edited or removed individually afterwards.           │
  │ [Approve]  [Reject]                                     │
  └─────────────────────────────────────────────────────────┘
```

Post-approve: "✓ Imported 58 items" + deep-link button → /vehicles/inventory.

## 3. Architecture — device-held file, transient server parse, staged rows

Two design rules, both owner directives + advisor-confirmed:

- **The FILE lives on the user's device** (same philosophy as the
  browser-local thought logs).  The server parses it in memory per turn
  and never persists it.
- **Row data never round-trips through the model** — it proposes the
  MAPPING; the server owns the data.

```
attach ──▶ browser reads the file as TEXT (FileReader), stores it
           DEVICE-LOCAL (thoughtStore-style, capped w/ eviction);
           composer shows a chip (name · size · ✕)

send   ──▶ file text rides INLINE on ChatRequest.attachments
           (fits the existing 2MB body cap)
             └─ server parses TRANSIENTLY (hardened parser, caps),
                grid lives only in the request scope
                (user_context["_attachment_grids"], the _db pattern) —
                NOTHING written to disk or DB

model  ──▶ read_attachment(name)        [READ tool, request-scoped]
             └─ shape + header + truncated sample ONLY (untrusted-framed)

model  ──▶ import_inventory_items(name, mapping_spec)  [WRITE, propose]
             mapping_spec = column INDICES → field semantics
             └─ SERVER applies mapping to the full in-memory grid
                → normalized rows + validation report → preview artifact
                → proposal stores the DERIVED ROWS (encrypted staged
                  payload, new un-truncated column) + mapping + counts.
                  The rows ARE the data the user is about to
                  write into Inventory — staging them briefly is
                  consistent with "files on device, data in DB".

approve ──▶ executor inserts FROM THE STAGED ROWS in ONE transaction
            (re-resolving vehicles against the registry at execute;
            newly-unmatched rows skipped + reported).  No attachment
            re-read needed — approve stays a no-body endpoint and works
            from any device.  Unapproved proposals prune on the normal
            proposal TTL; the staged rows go with them.
```

Integrity note: this is STRONGER than the server-file variant — the
preview is built from the very staged rows the executor will insert, so
there is no re-derivation gap to hash-guard.  What is deliberately
accepted: attachments are device-local (re-importing later needs the
device that has the file), and a pending proposal holds the derived rows
server-side for its short TTL — the irreducible minimum for a secure
no-body approve.

## 4. What gets created / modified

**Layering law (owner directive): the import machinery is UNIVERSAL.**
Nothing feature-specific lives in the generic layer; a future "import
fuel entries" or "import maintenance history" is a new ADAPTER, never a
fork of the pipeline.

**Generic layer (feature-agnostic, built once)**
- **No server-side file storage at all** (owner directive): the file
  text is DEVICE-LOCAL (browser storage, thoughtStore-style module with
  a total cap + oldest-first eviction); it travels inline on
  `ChatRequest.attachments: [{name, content}]` (per-field caps; fits
  the existing 2MB middleware ceiling) and is parsed transiently per
  turn.  No `ai_attachments` table, no disk bucket, no upload endpoint,
  no new retention target — the earlier server-file design is
  superseded.
- `capabilities/ai/attachments.py` — the universal pipeline, all
  request-scoped: hardened CSV→grid parser (strict utf-8-sig, streaming
  caps: rows ~2000 / cols ~64 / cell ~500, NUL-stripping), generic
  `apply_mapping(grid, mapping_spec) → records` engine (column INDICES,
  per advisor), and the `ImportTarget` registry — a feature registers
  `{name, field_vocabulary, build_rows(records), executor}` and the
  pipeline handles everything else.
- Router plumbing: parsed grids ride the request scope
  (`user_context["_attachment_grids"]`), never storage.  Attachment
  acceptance is gated like writes are: the request's attachments are
  parsed only for callers holding at least one `writes:True` tool
  permission (no free compute), and rejected on the `suppress_writes`
  surfaces.
- `read_attachment` read tool (generic; request-scoped;
  untrusted-sample framing: ~20 rows, ~80 chars/cell, delimited as
  data-not-instructions).
- `ai_action_proposals.staged_payload` — new encrypted TEXT column
  (`NOT NULL DEFAULT ''` as shipped)
  (migration; index-in-migration rule) holding the derived rows WITHOUT
  the 8k truncation of the legacy payload column.  Pruned with the
  proposal row by the existing 7-day sweep (pending ones die at TTL).
- **`import_preview` artifact type** (shared, in the artifacts registry
  home `features/ai/artifacts/`): `{columns, rows, skipped[], totals}` —
  one renderer reused by EVERY future import action.  The artifact
  system is already registry-based on both ends (backend `artifacts[]`
  envelope, frontend `registerArtifact` with unknown-type degradation) —
  this adds the first shared composite type to that contract.

**Feature adapter (per feature — Inventory is merely the first)**
- `features/vehicles/inventory/ai_actions.py` — registers the
  `inventory` ImportTarget: field vocabulary (item, category, status,
  identifier, note), vehicle resolution per §5.5, transactional
  executor over `add_inventory_item`, and the
  `import_inventory_items` write action (writes=True,
  `scope: 'account_unscoped'`, perm `can_manage_vehicles`).

**Modified (frontend)**
- `Chat.tsx` — `+` menu gains "Attach spreadsheet (CSV)"; hidden file
  input → upload → attachment chip above the toolbar (name · rows · ✕);
  `ChatRequest` gains `attachments: [{name, content}]` (file text read
  via FileReader; kept DEVICE-LOCAL in a thoughtStore-style module with
  a total cap + eviction; the chip re-sends it while attached).
- `client.ts` — attachment types on the stream call (no upload API —
  there is no upload endpoint).
- Router (backend) — attachment ids folded into the prompt context the
  same way `page_context` is (a hint; authorization stays JWT-side).

**Unchanged on purpose** — the approve card, artifacts registry, scope
gate, audit trail: the whole point is that they already work.

## 5. Security decisions — fable-advisor APPROVED, with tightenings

Verdict: *"Build it exactly as sketched — the mapping-not-data split is
the correct trust boundary: the model emits intent, the server owns
data, the human approves visible output."* The binding tightenings:

1. **Storage** — SUPERSEDED (owner directive, see §3 and §8): the
   advisor's option-a `ai_attachments` server table was replaced by
   device-held files + transient per-turn parsing; **no attachment
   storage exists server-side**.  What survived from this point: the
   integrity intent (now met more strongly by the staged rows — the
   executor writes exactly what the preview showed) and the clean-409
   rule (a proposal with unreadable staged data 409s at approve).
2. **Mapping split** confirmed, three tightenings: (i) mapping_spec
   addresses **column indices**, never header names (duplicate/blank
   headers break name addressing); (ii) the proposal payload stores
   `attachment_id + mapping_spec + grid sha256 + derived counts`, and
   execute re-derives and compares — **fail closed on drift**; (iii)
   the sample the model reads is truncated (~20 rows, ~80 chars/cell)
   and delimited as UNTRUSTED data — spreadsheet cells are a
   prompt-injection vector; blast radius stays "a bad mapping", which
   the preview catches **because the preview artifact is built
   server-side from the full grid by the SAME function the executor
   uses**.
3. **Bulk risk**: keep `risk='low'` (a 'high' classification dead-ends
   the feature while HIGH_RISK_WRITES_ENABLED is off). Instead: a hard
   server cap (~500 rows / 1000 items) enforced at parse AND execute;
   the approve card carries exact counts *including skips* ("Import 58
   items across 31 vehicles — 4 units skipped"). `account_unscoped` ✓ —
   into ACCOUNT_WIDE_TOOLS, NOT SCOPE_AWARE_TOOLS (the existing guard
   test enforces exactly that pairing). **All inserts in ONE
   transaction** — a mid-import crash plus the 409-on-non-pending rule
   would otherwise orphan partial imports with no retry path.
4. **Upload hardening**: strict `utf-8-sig` decode, reject undecodable
   (no charset guessing); real CSV reader with `field_size_limit`
   (quoted embedded newlines break naive counting); caps rows ~2000 /
   cols ~64 / cell ~500 enforced streaming with early abort; strip
   NUL/control chars; **`can_manage_vehicles` required on the upload
   endpoint itself** (defense in depth + no free storage); ~10
   uploads/hr/user. Formula injection (`=cmd()`) is NOT mangled at
   ingest — it's an export-layer concern; any future CSV exporter of
   inventory fields does the `=+-@` escaping.
5. **Resolution**: minimal normalization only (trim/casefold/collapse
   spaces); do **not** strip leading zeros ("022" ≠ "22"). Company-
   suffix parse only against the registry's enumerated company codes;
   a (number, company) pair must resolve to exactly ONE vehicle; a bare
   number matching multiple vehicles ⇒ ambiguous ⇒ skip. The preview
   shows the resolved registry vehicle beside each source row
   (approval is only meaningful if resolution is visible). Execute
   re-resolves; propose-matched-but-execute-unmatched units are skipped
   and reported in the result — never a whole-import failure.

Non-negotiables from the consult: don't touch the actions spine or
HIGH_RISK_WRITES_ENABLED; no manual-mapping UI in v1 (fix the sheet and
re-upload IS the loop); new-table indexes go in the migration, never in
platform_schema.py (the known boot-crash rule).

## 5b. File map — every location touched, by phase

```
PHASE A — transient attachment pipeline
  NEW  capabilities/ai/attachments.py            parser (CSV→grid, caps) +
                                                 apply_mapping engine +
                                                 ImportTarget registry
  MOD  capabilities/ai/router.py                 ChatRequest.attachments
                                                 [{name, content}] + transient
                                                 parse → user_context
                                                 ["_attachment_grids"] (stream
                                                 path only; suppress_writes
                                                 surfaces reject attachments)
  MOD  adapters/storage/platform_schema.py       ai_action_proposals gains
                                                 staged_payload column (fresh
                                                 installs; no index here)
  MOD  adapters/storage/platform_migrations.py   ADD COLUMN migration
  MOD  adapters/storage/ai_actions.py            mixin: store/read
                                                 staged_payload (encrypted,
                                                 un-truncated)
  MOD  capabilities/ai/actions.py                hand staged_payload to
                                                 executors alongside payload
  NEW  capabilities/ai/tests/test_ai_attachments.py              parser caps/BOM/quoted-
                                                 newlines; mapping engine;
                                                 registry

PHASE B — the read tool
  NEW  capabilities/ai/tools/attachments_tool.py read_attachment tool def
                                                 (in tools/, NOT in the engine
                                                 module — attachments.py must
                                                 stay importable by feature
                                                 adapters without dragging in
                                                 the tools hub / cycle risk)
  MOD  capabilities/ai/tools/registry.py         execute_tool injects request
                                                 grids as tool_args
                                                 ["_attachments"] for schemas
                                                 declaring uses_attachments
                                                 (the _scope_vehicles pattern;
                                                 model-supplied key stripped)
  MOD  capabilities/ai/tools/__init__.py         import attachments_tool so
                                                 @register_tool runs
  MOD  capabilities/ai/intelligence.py           3 agent loops pass
                                                 attachment_grids; profile
                                                 builders append the
                                                 attachment presence hint;
                                                 _TOOL_LABELS entry
  MOD  capabilities/ai/attachments.py            attachment_prompt_line()
  ---  capabilities/permissions/roles.py         (read_attachment needs no
                                                 perm row — request-scoped
                                                 data, gated at parse)

PHASE C1 — universal import framework
  (mostly capabilities/ai/attachments.py from A, matured)
  NEW  interfaces/dashboard/src/features/ai/artifacts/ImportPreviewArtifact.tsx
                                                 shared {columns, rows,
                                                 skipped[], totals} renderer
  MOD  interfaces/dashboard/src/features/ai/artifacts/index.ts   register it
  MOD  interfaces/dashboard/src/features/ai/artifacts/types.ts   type union

PHASE C2 — Inventory adapter (first ImportTarget)
  NEW  features/vehicles/inventory/ai_actions.py ImportTarget registration:
                                                 field vocabulary, vehicle
                                                 resolution (§5.5), ONE-
                                                 transaction executor,
                                                 import_inventory_items action
  MOD  capabilities/permissions/roles.py         TOOL_PERMISSIONS[import_…] =
                                                 can_manage_vehicles;
                                                 ACCOUNT_WIDE_TOOLS entry
                                                 (NOT scope-aware — guard test
                                                 enforces the pairing)
  MOD  capabilities/ai/tools/__init__.py         import the feature module
  NEW  features/vehicles/tests/test_ai_inventory_import.py         resolution rules, transaction
                                                 all-or-nothing, staged-rows
                                                 flow, re-approve idempotency
                                                 (pg_db)

PHASE D — frontend attach flow
  NEW  interfaces/dashboard/src/features/ai/attachmentStore.ts
                                                 device-local file store
                                                 (thoughtStore pattern: cap +
                                                 oldest-first eviction)
  MOD  interfaces/dashboard/src/features/ai/Chat.tsx
                                                 "+" menu → Attach CSV
                                                 (FileReader), chip row
                                                 (name · size · ✕), inline
                                                 send while chip attached,
                                                 post-import deep link
  MOD  interfaces/dashboard/src/api/client.ts    attachments on the stream
                                                 call types
  MOD  interfaces/dashboard/src/locales/*.json   (×7) attach/skip/import strings

PHASE E — verification
  MOD  docs/architecture/ai-import-assistant.md  as-built section (§ like
                                                 phase-4 doc got)
  loc  docs/ux-audits/<date>-ai-import.md        UX-psychology pass (local)
```

## 6. Roadmap

| Phase | Scope | Est. |
|---|---|---|
| **A** | Transient attachment pipeline: ChatRequest.attachments + hardened parser + request-scope plumbing + `staged_payload` migration + tests | ~⅓ day |
| **B** | `read_attachment` generic tool + prompt wiring (attachment presence hint) | ~2h |
| **C1** | **Universal import framework**: `apply_mapping` engine, `ImportTarget` registry, shared `import_preview` artifact (backend + frontend renderer), hash-drift fail-closed, transaction contract + tests | ~½ day |
| **C2** | **Inventory adapter**: field vocabulary + vehicle resolution + transactional executor + `import_inventory_items` action + tests | ~¼ day |
| **D** | Frontend: `+` attach flow (FileReader → device-local store), chip, inline send, post-import deep link | ~½ day |
| **E** | Rituals: design-system + UX-psychology passes, code-reviewer, live test with the REAL sheet | ~2h |

The C1/C2 split is the future-proofing contract: adding "import fuel
entries from a fuel-card CSV" later = one new C2-sized adapter (~¼ day),
zero changes to A/B/C1/D.

Out of scope v1 (recorded, not forgotten): `.xlsx` (needs openpyxl —
Google Sheets exports CSV natively), imports into other features
(maintenance tasks, fuel entries — the attachment/mapping machinery is
feature-agnostic by design; each is "one more write action" later),
bot/miniapp surfaces (`suppress_writes` already excludes them).

## 7. Test plan

- Parser: matrix fixture (the real sheet's shape) → grid; caps; BOM/UTF-8.
- Mapping engine: matrix→rows normalization; status_map application;
  notes preservation; unknown-vehicle skip list.
- Resolution: "103 OSY"/"103 G1" against a seeded registry; ambiguous
  numbers NOT matched.
- Proposal flow (pg_db): propose stores attachment ref not rows;
  preview artifact truncation; approve re-derives + inserts + audits;
  re-approve idempotent; foreign-account attachment → 404.
- Scope guard: new tool declares `account_unscoped` (existing CI test
  enforces the frozenset pairing).
- Upload endpoint: size/extension/row-cap rejections; rate limit.

## 8. As built (2026-07-16)

Shipped in five commits — 029681b (A) · 6768aaa (B) · 19df9ec (C1) ·
47b6bdf (C2) · c04d89e (D) · 1d3525a (UX polish).  Deltas from the plan
above, all recorded at the section they amend:

- **No upload endpoint / no server file at all** (the §3 revision won):
  attachments ride `ChatRequest.attachments` inline; the upload-endpoint
  test row and rate-limit row in §7 are moot.  The §5.2 grid-sha256 +
  re-derive-on-execute guard is also moot — the executor writes the
  proposal's **staged rows** (encrypted, un-truncated
  `ai_action_proposals.staged_payload`), the very rows the preview
  showed; there is no re-derivation to drift.
- **read_attachment lives in `capabilities/ai/tools/attachments_tool.py`**,
  not in the engine module — attachments.py stays importable by feature
  adapters without dragging in the tools hub (cycle risk).  The same
  module carries `propose_import()`, the generic propose-side any future
  import calls; grids reach tools via `execute_tool(attachment_grids=)`
  → `tool_args["_attachments"]` for `uses_attachments` schemas only
  (the `_scope_vehicles` server-channel pattern; a model-supplied key is
  stripped).
- **Attachment parse gate** tightened from "any writes:True permission"
  (vacuous — always-on derived flags give every role a write tool) to
  "holds a registered ImportTarget's permission", fail-closed when none
  are registered.
- **Resolution as specced** (§5.5): exact-match-first then
  company-suffix parse, exactly-one-or-skip, leading zeros preserved;
  execute re-resolves against the live registry and skips vanished
  units.  Status vocabulary is strict at build_rows (unmapped status =
  skip, never a defaulted "installed"); category soft-defaults to
  `other`.
- **One transaction** via `Database.transaction()` (the pool-proxy's
  `commit()` is a no-op inside it, so the storage mixin is reused
  unchanged); all-or-nothing verified by a fault-injection test.
- **Frontend**: pending attachments persist device-locally
  (`attachmentStore.ts`, thoughtStore quota discipline) and re-send on
  every message while the chip is attached — a follow-up turn ("now
  import it") still has the grid, since grids are request-scoped by
  design.  Composer holds an attached-state placeholder; store eviction
  is announced, never silent; preview titles carry the file name.
- **Tests**: `capabilities/ai/tests/test_ai_attachments.py` (21) +
  `features/vehicles/tests/test_ai_inventory_import.py` (8); the §7 "foreign-account
  attachment → 404" row became structurally impossible (no stored
  attachments to cross accounts).

Still open (v2 candidates): other-feature adapters, a post-import deep
link to the Inventory page from the done-state card.

**Update 2026-07-17 — Excel + drag-and-drop (live-test feedback):**
`.xlsx`/`.xls`/`.xlsm` are now supported WITHOUT touching the wire
contract: the browser converts workbooks to CSV text on the DEVICE
(`spreadsheet.ts`, SheetJS lazy-loaded as its own chunk), one attachment
per non-empty sheet ("Fleet sheet.xlsx — Trucks"); the picker accepts
CSV + Excel, multiple selection works, and files can be dragged onto
the composer (counter-based highlight, drop hint).  The earlier
"needs openpyxl" objection is void — no server dependency was added.

**Update 2026-07-17 (same day) — the TEXT-DOCUMENT lane:** attachments
now have two kinds on the wire (`ChatAttachment.kind`):

  * `sheet` (default) — spreadsheet text, eligible for IMPORTS, parse
    gated on a registered ImportTarget permission (unchanged).
  * `text` — extracted document text (PDF via pdf.js ON THE DEVICE,
    plain .txt).  READ-ONLY: the model reads it through
    `read_attachment`'s bounded 4k-char windows (`offset` pages through
    long docs), it can never feed an import, and it needs no import
    permission — attaching a document is the same trust level as typing
    its contents, and every tool call stays behind the normal gate.
    The `kind` field only selects the parser; a mislabeled attachment
    yields a failed parse or an inert text doc, never a privilege
    change.  PDF stays deliberately OUT of the import lane: a PDF is
    not a grid, and guessed rows written to Inventory are worse than
    no import.

The composer affordance is now "Attach document · CSV · Excel · PDF"
(+.txt), with per-kind chip icons.  Both extraction libraries (SheetJS,
pdf.js) are device-side lazy chunks — the wire contract is still
"derived text inline, nothing stored".
