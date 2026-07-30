# Vendor & Parts Master Data → Global Directory → Market Intelligence

**Status:** Phases A–B SHIPPED 2026-07-13 · Phase C1 (directory core:
platform table, operator curation on system.4truck.us, suggestion
queue, account linking) SHIPPED 2026-07-13 · C2-reviews (anonymous
stars/comments + operator moderation) SHIPPED 2026-07-13 · **C2-map
SHIPPED 2026-07-14** (prep + layer — see §5b: geo columns, operator
geocode/pin flow, popup contract, and the live-map "Repair Shops"
layer itself; miniapp port deliberately deferred) · D BUILT DARK
2026-07-13 behind MARKET_INTEL_ENABLED (toggle + nightly rollup job +
triple-gated reads + profile UI all shipped; flag stays OFF until the
legal review — open question #3 — clears)
**Decided:** 2026-07-13 (owner + Claude session; Phase A schema shape confirmed by
fable-advisor consult)
**Feature family:** Work Orders / Maintenance Ops (per-account layers) +
`capabilities/platform/` (global layers)

---

## 1. Vision

Today a work order records *how much* was spent, at *which shop*, on *which
vehicle* — as free text. This plan turns vendors and parts into first-class
entities with stable IDs, in four phases that build on each other:

| Phase | Layer | What it delivers | Who sees it |
|---|---|---|---|
| **A** | Per-account **vendor registry** | One record per shop; vendor profile page w/ full spend history; reports keyed by ID not name-string | That account only |
| **B** | Per-account **parts catalog** | One record per part; part history, cross-vendor price comparison *within the account* | That account only |
| **C** | **Global vendor directory** | Platform-curated shop identities (contact, services, map layer, anonymous ⭐ reviews); accounts link their private vendor records to directory entries | All accounts (identity data only) |
| **D** | **Anonymized market intelligence** | "27 companies used this vendor · brake-job parts typically $180–$220" on directory profiles | Accounts that opt in (give-to-get) |

End state: a parts/vendor price-intelligence network for trucking — something
neither Fleetio nor Samsara offers — built without ever exposing one company's
data to another.

## 1b. Feature placement (per the docs/FEATURES.md taxonomy)

- **Vendors = its own feature from birth**: `features/vendors/` (backend) +
  `src/features/vendors/` (frontend list + profile + merge).  Work Orders
  *references* it (`vendor_id`, picker); it never owns it.  Storage mixin
  lives in `adapters/storage/vendors.py` per the single-Database pattern.
- **Parts catalog = component of Work Orders** in Phase B (its only surface
  is the parts-editor autocomplete + reports).  It graduates to its own
  feature folder only if/when it grows a standalone management screen.
  **GRADUATED 2026-07-16** (owner decision): `features/parts/` +
  `src/features/parts/` with the standalone Parts page + per-part
  drill-down (recurrence per vehicle w/ mean-gap early warning, price
  per vendor, purchase history + trend).  Feature-owned permission
  `can_parts` (owner/admin/fleet + accounting senior tier) — NOT
  can_work_orders_all; the one shared read is `GET /parts` (list),
  which also accepts can_work_orders_all because it feeds the WO
  editor's autocomplete.  Wire moved `/work-orders/parts-catalog…` →
  `/parts…` with the old URLs as deprecated same-handler aliases
  (alias==primary test-pinned; the alias router registers BEFORE the
  WO router so `/{work_order_id}` can't capture "parts-catalog").
- **Global directory (C/D) = platform sub-family**:
  `capabilities/platform/vendor_directory/` — system-owner domain like
  Billing; curated on system.4truck.us; guarded by test_layer_boundaries.

## 2. Design principles (non-negotiable)

1. **Tenant isolation is absolute for transactions.** Prices, invoices, labor,
   purchase history never cross accounts in raw or attributable form.
2. **Invoice-truth snapshots.** `vendor_name/address/phone` (and part names)
   stay denormalized on work-order rows forever — editing a master record
   never rewrites what a historical invoice said. IDs are the analytical
   spine, names are the paper trail. (Same pattern as the vehicles registry:
   master table is SSOT, integrations enrich, display stays on rows.)
3. **App-level FKs.** Nullable INTEGER id columns + indexes, no declared FK
   constraints (consistent with the rest of the schema; keeps Datatruck sync
   non-transactional and resilient).
4. **Humans merge, code never fuzzy-matches.** Auto-linking uses exact
   normalized match only (`name_key` = trimmed, whitespace-collapsed,
   casefolded). Typo variants become duplicates that a human merges with the
   merge tool.
5. **RLS from birth.** Every new per-account table ships with the
   ENABLE/FORCE + `tenant_isolation` policy block (gated on `ENABLE_RLS`),
   and every query still carries an explicit `account_id = ?` predicate.
   (Note: `work_order_parts` predates this rule and has neither — do not
   copy its shape.)
6. **Global layers are platform-owned.** Directory tables live with the other
   system-owner domains; curation happens on system.4truck.us, never on the
   customer dashboard.
7. **Two shapes of global layer — don't converge them.** A *directory*
   (`vendor_directory`, `part_directory`) is an open-ended registry of
   real-world entities; tenant rows are born from the tenant's own activity
   and are **matched UP** to it by a nullable id (`global_vendor_id`,
   `global_part_id`). A *library* (`service_task_library`,
   `service_assembly_library`) is a closed curated vocabulary the platform
   **seeds DOWN** into every account, and the tenant row carries the
   library's `canonical_key` — a key, not a row id, so seeding is not
   order-dependent or environment-specific, survives a library row being
   recreated, and stays legible in historical line tags and the
   benchmarking `GROUP BY`. The naming difference (directory vs library,
   id vs key) is deliberate and signals which mechanism you're in.
   Reviewed and re-confirmed 2026-07-28 when the id/key asymmetry was
   questioned; the answer is that only the *behaviour* should match, and
   it does: identity fields are operator-owned, the fan-out/link fills
   empty fields only and never overwrites what a tenant typed, and
   operator approval adopts an account's matching row in place.
8. **The taxonomy is VMRS-shaped, and its L3 slot is reserved (owner
   decision 2026-07-28).** `adapters/storage/service_taxonomy.py` is the
   single home of the classification language, mapping onto VMRS level
   for level: `system_key` = CK31 System (`013`), `assembly_key` = CK32
   Assembly (`013-001`), and — **future, not built** — `component_key`
   = CK33 Component (`013-001-023`). The L3 name is `component_key`,
   NOT `part_key`: "part" is this schema's *entity* word (`part_id`,
   `parts_catalog`, `part_directory` — *which product*), "component" is
   the *type* word (*what kind of thing*), and VMRS itself names the
   level Component. Build triggers for L3: a VMRS licence (the licensed
   CK33 list then *becomes* the vocabulary, seeded into the taxonomy)
   or assemblies grown too fat to drill usefully — note our 112
   assemblies are deliberately finer than CK32 (`water_pump`,
   `thermostat`, `pads_shoes` are CK33-grade concepts), so most of the
   L3 granularity is already occupied. Adding it later is purely
   additive: new column on `parts_catalog`, vocabulary + suggest-matcher
   + bulk-apply chips, the same recipe `assembly_key` proved. Crucially,
   `part_directory` is NOT the CK33-analogue and never becomes one —
   classification ("what kind") is the taxonomy's job; the directory
   answers identity ("which one"). Same part row answers both, through
   different columns.

### VMRS adoption runbook (owner intent 2026-07-30: license later — this is the whole migration)

When the TMC/ATA licence is purchased, VMRS arrives as DATA, never as
renames. Nothing in this list touches an existing name, wire key, or
report:

1. **New module `adapters/storage/vmrs.py`** — honestly named, because
   it will hold actual VMRS material: the licensed CK31/CK32 mappings
   (`{"brakes": "013", ...}` — one of ours may map to several of
   theirs) and the CK33 component list.
2. **Mapping columns, additive** — each `VEHICLE_SYSTEMS` /
   `SERVICE_ASSEMBLIES` entry gains a `vmrs` code via that module.
   Our slugs stay primary (environment-independent, readable in
   GROUP BY and history); the code is a projection.
3. **`component_key` builds** (principle 8's reserved L3 slot): the
   licensed CK33 list IS the vocabulary — seeded into
   `service_taxonomy`, column on `parts_catalog`, suggest-matcher +
   bulk-apply chips, the same recipe `assembly_key` proved.
4. **Reports/exports emit codes beside labels** — the same GROUP BY
   gains a `vmrs_code` column through the mapping; historical rows
   convert for free because every row already carries
   `system_key`/`assembly_key`.

Deliberately rejected, twice (2026-07-28 advisor on `canonical_key`,
2026-07-30 owner review): renaming `system_key`/`assembly_key`/
`component_key` to `ck31/ck32/ck33`, numbering our own entries in
VMRS's `013-001-023` format, or naming our taxonomy module "vmrs"
before it holds licensed content. A name that promises VMRS semantics
over non-VMRS values is a false friend in every export and
integration; and the eventual migration contains no rename step, so
the churn would buy nothing. Name follows content — in both
directions.

---

## 3. Phase A — Per-account vendor registry (approved)

### Schema (migration 149)
```
vendors (
  id          INTEGER PK,
  account_id  INTEGER NOT NULL,          -- + RLS block + explicit predicates
  name        TEXT NOT NULL,             -- display casing
  name_key    TEXT NOT NULL,             -- normalized; UNIQUE(account_id, name_key)
  address     TEXT NOT NULL DEFAULT '',
  phone       TEXT NOT NULL DEFAULT '',
  email       TEXT NOT NULL DEFAULT '',
  notes       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL DEFAULT ''
)
ALTER TABLE work_orders ADD COLUMN vendor_id INTEGER;   -- nullable + index
```
`name_key` is a stored column, not an expression index — dialect-safe through
the SQLite-compat layer, and the uniqueness target for concurrent-sync upserts
(`ON CONFLICT (account_id, name_key) DO NOTHING` then select).

### Backfill (same migration)
Per account: one vendor per distinct non-empty `name_key` across existing
work orders; display name = most frequent casing; address/phone = most recent
non-empty. **Link ALL rows** (deterministic on `name_key`; no unlinked
leftovers — ambiguity at 366 rows is cosmetic and fixable via merge).

### Sync (Datatruck)
`project_external_work_orders` resolves vendor by `(account_id, name_key)`
upsert → sets `vendor_id`. Idempotent across re-syncs.

### Merge tool (v1-mandatory)
`POST /vendors/{loser_id}/merge-into/{winner_id}` — both ids validated against
the caller's account, repoints `work_orders.vendor_id`, deletes loser, audit
log. One "Merge into…" button on the vendor page. (Auto-create guarantees
eventual typo-duplicates; without merge they'd be permanent.)

### UI
- **Vendor picker** on the work-order form: registry-first, free-type creates
  (same UX as the vehicle picker). Picking one auto-fills the snapshot
  address/phone fields.
- **Vendors list + vendor profile page** (per account): contact block, notes,
  spend summary, and the full work-order history — "open a vendor → see every
  part bought, at what price, with what labor." This page is the Phase-A
  user-visible payoff.
- **Reports:** `cost_by_vendor` groups by `vendor_id` (residual per-raw-name
  bucket where NULL — effectively dead after backfill, kept for safety).

### Acceptance
- Every existing WO has `vendor_id` set post-migration (where vendor_name ≠ '').
- Re-running a Datatruck sync creates zero duplicate vendors.
- Merging two vendors moves all WOs and survives a re-sync (the synced name
  re-resolves to the surviving vendor's name_key or recreates — decide:
  merge stores loser's name_key as an alias row? → **open question #1**).
- pg tests: backfill, upsert race (two inserts same name_key), merge scoping
  (cross-account merge attempt 404s), report rekey.

## 4. Phase B — Per-account parts catalog (after A)

Same recipe, applied to parts:
```
parts_catalog (
  id, account_id NOT NULL, name, name_key UNIQUE(account_id, name_key),
  part_number TEXT DEFAULT '', notes, created_at, updated_at
)  -- + RLS from birth
ALTER TABLE work_order_parts ADD COLUMN part_id INTEGER;  -- nullable
```
- Backfill from distinct `LOWER(part_name)` (replaces the report's LOWER()
  heuristic with real identity).
- Parts editor gains autocomplete against the catalog; free-type creates.
- Account-private analytics unlocked: part history across vendors ("we paid
  $50/pad at Shop A, $55 at Shop B"), true part recurrence per vehicle.
- Merge tool reused (same endpoint pattern).
- **Not** stock inventory: no on-hand quantities, ever (out of scope — our
  customers don't run warehouses).

## 5. Phase C — Global vendor directory (designed, not scheduled)

Platform-owned identity layer ("Google Maps for truck repair shops"):

- `vendor_directory` (platform table, **no account_id**): name, normalized
  key, address(es), geo, phone, email, website, services offered, status
  (active/pending/rejected), created_by (operator | suggestion).
- `vendors.global_vendor_id` (nullable) — each account may link its private
  vendor record to a directory entry. Linking is suggested automatically on
  exact normalized match, confirmed by the user.
- **Curation:** system.4truck.us gets a Directory section (list, edit, merge,
  approve suggestions) — same operator-console pattern as accounts/comp.
- **User suggestions:** "Suggest this vendor to the directory" from the
  account vendor page → approval queue on the operator console. Only
  identity fields travel; never any account transaction data.
- **Map layer:** directory vendors as a POI layer on the live map
  (architectural sibling of the existing parking POI layers) — drivers see
  nearby shops + services.
- **Reviews/stars:** account users rate directory vendors; displayed
  anonymously ("a fleet from Illinois · 12 trucks"). Moderation queue on the
  operator console. (**Open question #2:** review/dispute policy.)

## 5b. C2-map prep (shipped 2026-07-14) + remaining build

Prep that landed ahead of the map layer, per the live-map architecture
audit:

- **Geo columns:** `vendor_directory.lat/lng` (nullable REAL, platform
  migration `migrate_vendor_directory_geo`).  Set ONLY via
  `set_directory_geo()` — the generic entry update never touches
  coordinates.  Entries without coordinates never appear on map layers.
- **Operator geocode/pin flow** (system.4truck.us → Vendor Directory →
  Location): server proxies Nominatim (`GET
  /system/vendor-directory/geocode`, operator-gated, 1h cache, results
  are suggestions only) → operator picks/adjusts → confirms via `PUT
  /system/vendor-directory/{id}/geo` (both-or-null validation).  The
  pin preview is a dependency-free OSM embed iframe.
- **Generic popup contract:** `PoiLayerDef.popup?(feature, def)` in
  `config/poiLayers.ts`; the engine (`usePoiLayers.renderLayer`)
  dispatches `def.popup ?? defaultOsmPopup`.  DB-backed layers supply
  their own popup — never add layer-specific branches in the engine.

C2-map layer (SHIPPED 2026-07-14, same session as prep):

1. `directory_entries_in_bbox()` on VendorDirectoryMixin — ACTIVE +
   lat/lng NOT NULL + bbox predicate, identity fields only.
   features/location calls the storage method on the shared Database;
   it does NOT import capabilities.platform modules (layer boundary
   verified by test_layer_boundaries).
2. `vendor_directory` source branch in `map_pois()`
   (features/location/pois.py) — platform-global data, tenant-agnostic
   TTL cache key correct as-is.  Freshness: server cache 5 min +
   client localStorage up to 2 h → a newly geocoded shop can take up
   to ~2 h to appear for a client that recently viewed the area.
3. `POI_LAYERS` entry "Repair Shops" under the new "Services" group
   (lucide Wrench, green #16a34a) with `vendorDirectoryPopup` —
   identity-only v1 (name, service badges, address, phone, website;
   HTML-escaped, http(s)-only links).  Stars/usage counts stay behind
   the manager-gated vendor endpoints; widening them to all map users
   is a separate product decision.
4. Miniapp port SHIPPED 2026-07-14: a "repair shops" FAB on the driver
   map (Telegram-native @vkontakte hammer icon) toggles the same
   /map/pois?type=vendor_directory layer — bbox-scoped fetch, debounced
   refetch on pan, identity-only popup, marker styling identical to the
   dashboard layer.

## 5c. Directory UX + data-quality pipeline (shipped 2026-07-14)

Closed the gaps from the end-to-end flow audit (user vendor → suggest →
operator verify → public):

- **Vendor edit dialog** on the profile page (backend PUT existed, UI
  didn't — registry records were uneditable by accident).
- **Enrich-on-save**: `resolve_or_create_vendor` fills an existing
  vendor's EMPTY contact fields from later work-order saves / Datatruck
  sync (was `ON CONFLICT DO NOTHING` → info discarded forever).  Never
  overwrites set values; WO snapshots untouched.
- **Suggestion quality gate**: suggest-to-directory 422s without an
  address; the dashboard collects address/phone/email in a completion
  dialog (write-back PUT then suggest) so operators receive verifiable
  suggestions, not name-only stubs.
- **On-link enrichment**: linking copies the operator-verified entry's
  identity into the vendor's empty fields (global quality flows down).
- **Directory browse tab** on the Vendors page (Team-Management surface
  -tab pattern): active entries + anonymous rating aggregate + the
  caller's own link status (`GET /vendors/directory/browse`,
  `browse_directory()`fetches only the caller's link — nothing else
  account-specific).

## 5c-bis. AUTO pipeline (SHIPPED 2026-07-15 — supersedes the manual
## Link/Suggest ceremony from §5c)

Owner decision: the directory collects itself; users never click
"Suggest" or "Link".  The flow is fully automatic:

1. Truck gets service → vendor exists (WO save / Datatruck sync — as
   before).
2. The moment a vendor's identity is complete enough to verify
   (non-empty address — arriving via enrich-on-save or the Edit
   dialog), ``autosuggest_vendor`` feeds name/address/phone/email into
   the platform review queue.  Identity only; idempotent on the global
   name_key; rejected tombstones still block re-spam.
3. Operator approves (+ geocodes) on system.4truck.us →
   ``adopt_matching_vendors`` links EVERY account's unlinked vendors
   with that name_key and fills their empty contact fields.
4. New accounts that start using an already-approved shop auto-link at
   vendor-resolve time.

UI: the vendor profile shows pipeline STATE only (linked / sent for
review / waiting for an address); Unlink remains as the correction
valve.

**Manual suggest REMOVED (2026-07-16):** `POST /{id}/suggest-to-
directory` is gone — no UI caller, and it bypassed the account's
share_vendor_identities consent (auto-only closes that hole).
Contribution is auto-ONLY, permanently.

**The dedup carve-out (owner decision, same day):** dedup RESOLUTION is
human work, and it lives in the merge dialog with two scopes and two
verbs — "Your vendors" → destructive fold (`/{loser}/merge-into/
{winner}`, which now carries the loser's directory link to an unlinked
survivor), "Public directory" → non-destructive LINK
(`POST /{id}/link-directory/{entry}`, link+adopt-empty-fields, for when
the auto name-match couldn't see the duplicate: "TA Dallas" vs "TA
Travel Center #241"; reversible via `DELETE /{id}/link-directory`).
Advisor guardrails: the two id spaces NEVER share an endpoint (merge
stays fold-only), the confirm button says the real verb (Merge vs
Link), and adopting never renames the local vendor.

**Parts public side — BUILT 2026-07-16 (owner overrode the advisor's
deferral; advisor then ruled the minimal safe scope, which is what
shipped):** a `part_directory` platform family mirroring vendors WHERE
the mirror makes sense, minus everything a part name can't be:

- **Tables (platform):** `part_directory` (canonical identities:
  name/name_key UNIQUE/category/part_number/description, status
  active|archived, source manual|import|promoted — no geo, no reviews,
  no chain, no pending), `part_directory_aliases` (operator-mapped
  variants; an alias key may NEVER equal an entry key — write-time
  rejected), `part_directory_dismissals` (candidates-queue
  tombstones).  Tenant: `parts_catalog.global_part_id` +
  `public_link_suppressed` (migration 155).
- **NO user contribution pipeline** — parts have no "address present"
  quality gate, so curation is TOP-DOWN only: console create / import
  / one-click PROMOTE from the **candidates queue** (cross-account
  name_keys used by ≥2 accounts matching nothing yet; operator-eyes
  only; dismissals tombstone forever).
- **GENERIC-KEY BLOCKLIST** (`GENERIC_PART_KEYS`, the 6-month-bite
  guardrail): "labor" / "shop supplies" / "fee"… can never become
  canonical — excluded from candidates, adoption AND creation.  One
  bad promote would pool unlike things and later poison Phase D
  ranges; with no geo pin to disambiguate, the blocklist is the brake.
- **Adopt fan-out** (`adopt_matching_parts`, fires on create /
  activate / alias-add) + **resolve-time autolink** for parts created
  after curation.  Fill-empty `part_number` ONLY — category displays
  through the join, name/notes stay user vocabulary (stricter than
  vendors, deliberately).  **Unlink SUPPRESSES**: adopt's predicate is
  `global_part_id IS NULL AND NOT public_link_suppressed`, so a
  user's unlink survives every re-fire; explicit re-link clears it.
- **Surfaces:** console → system.4truck.us "Parts directory"
  (candidates promote/dismiss, entries CRUD, aliases, archive, TSV
  import); dashboard → Parts page "My parts / Public catalog" tabs
  (browse = ACTIVE identity + own link state, can_parts), part
  profile public-link banner + Unlink, and the dedup dialog's second
  scope "Public catalog" (Link verb — same two-verb pattern as
  vendors).
- **Phase D key flip — DONE pre-launch (2026-07-18):** part cells now
  pool by `gp:<global_part_id>` when linked (canonical label; variants
  finally clear the 3-company rule as one cell), normalized-name
  fallback for unlinked, and unlinked GENERIC names form no part cells
  at all.  Executed while dark per the advisor's sequencing rule —
  re-keying after launch would visibly shift published ranges.

## 6b. Market intel — own platform family + console launch + geo cells
## (SHIPPED dark, 2026-07-18)

Owner directives executed the same day:

- **Family consolidation:** market intel is neither Vendors' nor
  Parts' — it lives at `capabilities/platform/market_intel/` (router
  + jobs; the nightly job moved out of vendor_directory/jobs.py).
  The env-twin hack is DEAD: the launch gate is
  `Database.market_intel_enabled()` (adapters/storage/
  platform_settings.py) — a shared-Database method both layers may
  legally call, backed by the `platform_settings` table with the
  `MARKET_INTEL_ENABLED` env var as an emergency override only.
- **Console launch cockpit** (system.4truck.us → "Market intel"): the
  ON/OFF switch (propagates ≤60s, no restart, no SSH), readiness
  numbers (sharing accounts / vendor cells / part-geo cells / last
  computed), a "Compute rollups now" preview (safe while dark — cells
  stay invisible until the switch opens), and the chain explainer.
  The .env ritual is gone from the launch runbook.
- **Part-centric GEOGRAPHIC estimates** (owner idea): new
  `market_part_rollups` platform table — (catalog part × national |
  state) cells from the same nightly pass, state parsed from the
  shop's curated directory address.  ONLY catalog-linked parts pool
  (one more reason curation matters); same six hard rules; city tier
  deliberately deferred (3+ sharing companies per city per part is a
  high bar — state lights up first).  Surfaces: "Estimated market
  price" card on the part profile (national + state chips;
  give-to-get pitch with count when not sharing; hidden while dark or
  unlinked) and an "Est. Price" column on the Catalog tab (national
  range, sharing accounts only).  The VENDOR-side ranges are
  unchanged — the two answer different questions ("is this quote
  fair?" at the shop vs "what should this cost around me?" before
  choosing one).

Account-side (shipped same day): `POST /parts` Add-part (resolve
semantics + honest `created` flag) and visible part IDs (#id chip on
the profile, hidden-by-default ID grid column) for unambiguous merge
bookkeeping.

Map: two Services layers — "Repair Shops" (public directory, green) and
"My Vendors" (the caller's auto-linked shops, blue, own-vendor name in
the popup; ``my_vendors`` branch in pois.py with an ACCOUNT-SCOPED
cache key — the tenant-cache rule applies to this one).

Known trade-off (accepted): cross-account auto-link matches on the
exact normalized name.  Generic names ("Joe's Truck Repair") could
collide across different physical shops; operator naming discipline
(numbered/city-qualified names, §5d) keeps this rare, and Unlink +
operator entry-rename are the correction paths.

## 5d. Chain support (foundation SHIPPED 2026-07-15; rest triggered)

Multi-location brands (TA/Petro, Love's, Speedco…) are **one directory
entry per LOCATION** — forced by the single geo pin and the global
UNIQUE name_key, and matching how the chains name themselves (official
site numbers: "TA Tuscaloosa #0016"; Datatruck invoices carry them,
e.g. "Petro Ontario #0026").  Canonical entry name:
`<Brand> #<number> – <City>, <ST>`.

**Foundation (shipped):** `vendor_directory.chain` label ('' =
independent), operator-set only (create row + edit on system.4truck.us,
datalist of existing labels to prevent spelling forks); rides every
account-facing read; shown as a badge in the Directory tab and in both
map popups (dashboard + miniapp).  Suggestions never carry a chain —
classifying brands is operator curation.

**Deferred pieces — build when their trigger fires:**
1. Directory-tab grouping + live-map chain filter chips — when the
   directory holds ~20+ chain locations.
2. Market-intel CHAIN pooling (rollup cells keyed on chain, meeting the
   ≥3-companies rule across a brand's locations) — when Phase D's flag
   goes live.
3. Operator bulk "Import chain locations" — SHIPPED 2026-07-15 (the
   trigger fired early: a payload audit proved Datatruck sends vendor
   NAME ONLY — 233 vendors, zero addresses — so user-side data can
   never feed the queue; curation must flow top-down).
   ``import_directory_entries`` storage + POST
   /system/vendor-directory/import (≤2000 rows) + console UI
   (spreadsheet TAB-paste; entries born active+geocoded+chain-labelled;
   matching vendors adopt immediately; existing names skip).  Console
   also gained search + show-more paging for the larger directory.
   Initial seed: OSM/Overpass brand extracts (Love's, TA/Petro,
   Speedco, Blue Beacon, Southern Tire Mart) imported through the real
   pipeline.  OSM is start-point data — operators refine names/
   addresses per §2's verify-before-public rule; Overture remains the
   richer future source.

## 6. Phase D — Anonymized market intelligence (designed, not scheduled)

The crown: directory vendor profiles show price context computed from
participating accounts' work orders.

**Display (per service task and/or common part):**
> Brake job · parts **$180 – $220** *(typical range · 27 invoices from 9
> companies · last 12 months · prices vary by volume and situation)*

**Hard rules — all six ship together, none optional:**
1. **≥ 3 distinct companies** per aggregate cell, else "Not enough data yet."
   (Below 3, an "aggregate" is someone's actual invoice with the name
   removed; at 2, each party can deduce the other's.)
2. **Typical range, not raw min–max** — trimmed (p25–p75) endpoints so one
   emergency road-call invoice doesn't stretch the range into "$180–$900".
   The *presentation* is still a range (the "$180 vs $200 confusion" the
   range is meant to solve), just computed robustly, with the count and the
   prices-vary disclaimer alongside.
3. **12-month rolling window** — stale prices mislead buyers and defame
   vendors.
4. **Give-to-get:** an account-level toggle; market ranges are visible only
   to accounts sharing their own anonymized data. This is the consent
   mechanism AND the growth engine.
5. **Anonymous always:** no company names, no per-invoice rows, no personal
   data (drivers, uploaders) anywhere in the shared layer.
6. **Directory prerequisite:** aggregates key on `global_vendor_id` — without
   Phase C identity resolution, every shop's data splits across name variants
   and nothing passes the 3-company rule.

**Mechanics:** nightly rollup job (data_lifecycle-style fan-out) computes
`(global_vendor_id, service_task | part_key, window) → {companies, invoices,
p25, p75}` into a platform table; profiles read the rollup, never raw tenant
rows. (**Open question #3:** legal review of aggregated-pricing display
before launch — flag to counsel; engineering posture is conservative by
design.)

## 7. Sequencing & dependencies

```
A (vendor registry) ──► B (parts catalog) ──► C (directory) ──► D (market intel)
        │                      │                    │
        └── vendor page        └── price-per-part   └── map layer, stars
            (account-private analytics)                 (identity only)
```
- A and B are pure per-account features — valuable standalone even if C/D
  never ship.
- C depends on A (something to link FROM); D depends on B + C (structured
  prices + one identity per shop).
- Rough sizing: A ≈ the service-task layer just shipped (1 focused session);
  B slightly smaller; C is a real product increment (operator UI + map +
  moderation); D is medium engineering + the legal/product review.

## 8. Open questions

1. **Merge + re-sync:** should merging vendors keep the loser's `name_key` as
   an alias so the next Datatruck sync re-resolves to the winner instead of
   recreating the loser? (Recommended: yes — small `vendor_aliases` table;
   decide in Phase A implementation.)
2. **Review moderation policy** (Phase C): who approves, dispute path for
   vendors, profanity/defamation handling.
3. **Legal review** (Phase D): aggregated price display across customers —
   confirm with counsel before launch.

## 9. Explicitly out of scope

- Stock/on-hand inventory and stockout tracking (customers don't run
  warehouses; vendors do).
- Cross-account visibility of any raw transaction, price, or company-
  attributable fact — in every phase, forever.
- Fuzzy/AI name matching for auto-link (humans merge; code matches exactly).
