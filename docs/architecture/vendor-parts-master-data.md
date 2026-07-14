# Vendor & Parts Master Data → Global Directory → Market Intelligence

**Status:** Phases A–B SHIPPED 2026-07-13 · Phase C1 (directory core:
platform table, operator curation on system.4truck.us, suggestion
queue, account linking) SHIPPED 2026-07-13 · C2-reviews (anonymous
stars/comments + operator moderation) SHIPPED 2026-07-13 · C2-map (POI
layer) parked pending live-map POI prep · D BUILT DARK 2026-07-13
behind MARKET_INTEL_ENABLED (toggle + nightly rollup job + triple-gated
reads + profile UI all shipped; flag stays OFF until the legal review —
open question #3 — clears)
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
