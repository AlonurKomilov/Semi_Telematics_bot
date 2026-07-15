# Reports Module — Hierarchy & Organisation Audit

> **As of 2026-06-03.** Companion to [reports-module-audit.md](reports-module-audit.md)
> (which catalogues WHAT exists). This document addresses HOW the
> Reports module is *organised* across the sidebar, URL space, backend
> route files, and shared page chrome — and HOW it could be
> reorganised so "Reports" feels like a module instead of four loosely-
> grouped pages.

## 1. Today's State

### 1.1 Sidebar matrix — what each persona renders inside `nav.reports_group`

All six persona nav files declare a single flat `NavGroup` whose
`titleKey: 'nav.reports_group'` carries the four "Reports & analytics"
items as sibling `NavItem`s. There is no nested expandable shape.

| Persona | File:line | `Reports` | `Risk Summary` | `Cost Reports` | `Scheduled Reports` | Other items in same group |
|---|---|---|---|---|---|---|
| default (Owner/Admin) | `defaultNav.ts:81-88` | ✓ | ✓ | ✓ | ✓ | — |
| fleet | `fleetNav.ts:66-74` | ✓ | ✓ | ✓ | ✓ | `Cost per Mile` (`/costs/cpm`) |
| safety | `safetyNav.ts:56-62` | ✓ | ✓ | ✗ | ✓ | — |
| dispatch | `dispatchNav.ts:63-69` | ✓ | ✗ | ✗ | ✓ | `Fuel Costs` (`/costs/fuel`) |
| hr | `hrNav.ts:67-73` | ✓ | ✓ | ✗ | ✓ | — |
| accounting | `accountingNav.ts:33-43` | ✓ | ✗ | ✓ | ✓ | `Fuel Costs`, `Cost per Mile`, `Payroll` (label reused for "Costs") |

- Order is inconsistent: Safety puts Risk Summary first
  (`safetyNav.ts:58`); default lists Reports → Risk Summary → Cost
  Reports → Scheduled (`defaultNav.ts:83-86`); fleet lists Reports →
  Risk Summary → Scheduled → CPM → Cost Reports (`fleetNav.ts:68-72`);
  accounting puts Fuel/CPM/Cost-Reports/Payroll **above** Reports
  itself (`accountingNav.ts:35-42`).
- Accounting overloads the `nav.reports_group` label for non-report
  items (`accountingNav.ts:30-32` comment acknowledges: "Uses the
  `reports_group` label since these items live in /costs/ and
  /cost-reports structurally").
- HR's group includes `Reports` itself, but HR `FeatureSet` doesn't
  grant `can_faults` so the link renders but 403s on click (cross-ref
  reports-module-audit A4).

### 1.2 Nav data shape — can it nest?

`defaultNav.ts:34-44` defines flat two-level types:
```ts
export interface NavItem { labelKey; path; icon; permission; }
export interface NavGroup { titleKey: string | null; items: NavItem[]; }
```
`Sidebar.tsx:113-154` walks exactly two levels (`navConfig.map` →
`group.items.map`). **No support for nested groups today.**

### 1.3 URL matrix

| Page | URL | router.tsx line | File path |
|---|---|---|---|
| Reports (tabbed) | `/reports` | `:167` | `pages/reports/Reports.tsx` |
| Scheduled Reports | `/reports/scheduled-reports` | `:168` | `pages/reports/ScheduledReports.tsx` |
| Risk Summary | `/reports/risk-summary` | `:172` | `pages/reports/RiskSummary.tsx` |
| Cost Reports | `/cost-reports` ⚠️ | `:195` | `pages/reports/CostReports.tsx` |

Existing redirects: `:171` `/reports/subscriptions` →
`/reports/scheduled-reports`; `:218` `/admin/inspection-template` →
`/inspections?tab=template`. **No `/cost-reports` redirect today.**

### 1.4 Backend route matrix

| Endpoint | URL prefix | File:line | Permission |
|---|---|---|---|
| Faults | `/reports/faults` | `reports.py:34` | `can_faults` |
| Fuel levels | `/reports/fuel-levels` (+ legacy `/reports/fuel`) | `reports.py:65,110` | `can_fuel` |
| Health | `/reports/health` | `reports.py:119` | `can_health` |
| Efficiency | `/reports/efficiency` | `reports.py:139` | `can_efficiency` ∨ `can_vehicle_all` |
| Export | `/reports/export` | `reports.py:166` | any-of + per-type re-check |
| Risk Summary | `/reports/risk-summary` + `/risk-summary/me` | `reports.py:236,329` | `can_risk_report_all` ∨ `_own` |
| Cost Reports — per-vehicle | `/work-orders/reports/per-vehicle` ⚠️ | `work_orders.py:524` | `can_cost_reports` |
| Cost Reports — per-task-type | `/work-orders/reports/per-task-type` ⚠️ | `work_orders.py:537` | `can_cost_reports` |
| Cost Reports — per-vendor | `/work-orders/reports/per-vendor` ⚠️ | `work_orders.py:550` | `can_cost_reports` |
| Cost Reports — summary | `/work-orders/reports/summary` ⚠️ | `work_orders.py:564` | `can_cost_reports` |
| Cost Reports — monthly trend | `/work-orders/reports/monthly-trend` ⚠️ | `work_orders.py:620` | `can_cost_reports` |
| Scheduled Reports GET/PUT/DELETE | `/user/scheduled-reports` ⚠️ | `user.py:261,284,329` | `can_digest` |

### 1.5 Command-palette registry

`routeRegistry.ts:51-60` defines a third "Reports group items"
registry. It includes Reports, Risk Summary, Scheduled Reports, **Fuel
Costs**, **Cost per Mile** — but **omits Cost Reports entirely**.
Command-K search for "cost reports" returns no hit.

### 1.6 PageHeader chrome — divergent presentations

| Page | File:line | Icon | Header actions | Width |
|---|---|---|---|---|
| Reports | `Reports.tsx:214-242` | `FileText` | `DateRangePresets` (efficiency tab only) + PDF + CSV | full |
| Risk Summary | `RiskSummary.tsx:167-172` | `FileText` | none — PDF/CSV in body | `max-w-2xl` |
| Cost Reports | `CostReports.tsx:239-265` | `TrendingUp` | period `<select>` + CSV (no PDF) | full |
| Scheduled Reports | `ScheduledReports.tsx:416-420` | `Mail` | none (page is a form) | `max-w-xl` |

Three export patterns: server-side via `/reports/export`
(`Reports.tsx:192-211`), server-side per-form
(`RiskSummary.tsx:120-164`), **client-side CSV assembly**
(`CostReports.tsx:178-219`).

### 1.7 Cross-page navigation

**No Reports-shell sub-nav, breadcrumb, or tab bar at the shared
level.** Reports's tab bar (`Reports.tsx:245-257`) only spans its four
in-page sub-reports — does not cross-link to Risk Summary, Cost
Reports, or Scheduled Reports. The three sibling pages have no
in-page nav back to the rest.

### 1.8 Dashboard `data/reports.ts` registry

`data/reports.ts:28-34` declares only the **5 recurring/scheduled**
reports (faults/fuel/health/efficiency/camera) — by design, mirroring
`capabilities/reporting/registry.py` (`data/reports.ts:1-11` documents
this). Risk Summary + Cost Reports are correctly excluded but the
file name is generic enough that a reader could assume it's the
canonical SSOT for ALL reports.

## 2. Inconsistencies Catalogued

| # | Inconsistency | Severity | Evidence |
|---|---|---|---|
| H1 | Cost Reports URL top-level (`/cost-reports`) while page is in `pages/reports/` | 🔴 | `router.tsx:195` |
| H2 | Cost Reports backend at `/api/work-orders/reports/*`, not `/api/reports/*` | 🔴 | `work_orders.py:524,537,550,564,620` vs `CostReports.tsx:105-122` |
| H3 | Scheduled Reports backend at `/api/user/scheduled-reports` (defensible but adds 3rd prefix) | 🟡 | `user.py:261,284,329` |
| H4 | No shared Reports-shell layout — 4 pages × divergent chrome | 🟡 | `Reports.tsx:214`, `RiskSummary.tsx:167`, `CostReports.tsx:239`, `ScheduledReports.tsx:416` |
| H5 | No cross-page nav inside Reports | 🟡 | none of the 4 pages import a shared layout |
| H6 | Sidebar data model is flat — no `children` slot | 🟡 | `defaultNav.ts:41`, `Sidebar.tsx:121` |
| H7 | Per-persona ordering of `reports_group` differs | 🟡 | `defaultNav.ts:83`, `safetyNav.ts:58`, `accountingNav.ts:35` |
| H8 | Accounting overloads `nav.reports_group` for /costs/ items | 🟡 | `accountingNav.ts:31-43`, `en.json:163` |
| H9 | `/reports` visible to all personas but gated `can_faults` (HR/Accounting 403) | 🟡 | `router.tsx:167`, 6 nav files |
| H10 | `routeRegistry.ts` lacks Cost Reports entry | 🟢 | `routeRegistry.ts:51-61` |
| H11 | `data/reports.ts` generic name covers only scheduled-delivery 5 | 🟢 | `data/reports.ts:1-34` |
| H12 | PDF/CSV export pattern diverges across the 4 pages | 🟢 | see §1.6 |
| H13 | No `/cost-reports` → `/reports/cost-reports` redirect today | 🟢 | `router.tsx:166-220` (absent) |

## 3. Reorganisation Options

### Option A — Sidebar-only nesting (S/M, ~6-10h)

Add `children?: NavItem[]` to `NavItem` (`defaultNav.ts:34-44`); teach
`Sidebar.tsx:121-150` recursive render with expand caret + indented
children + collapsed-parent state. Replace flat siblings with one
nested parent per persona nav file.

**Files:** 6 `*Nav.ts` + `Sidebar.tsx`.

**Risk:** active-route highlighting for parent-when-on-child,
expand-state persistence (where? localStorage per group?), perm-filter
recursion (hide parent when all children hidden).

**Doesn't solve:** URL inconsistency, backend split, per-page chrome.

### Option B — URL canonicalisation (M, ~4-6h)

1. `router.tsx:195` → `<Route path="reports/cost-reports" …>`; add
   `<Route path="cost-reports" element={<Navigate to="/reports/cost-reports" replace />} />`.
2. Update path values: `defaultNav.ts:85`, `fleetNav.ts:72`,
   `accountingNav.ts:37`.
3. Backend: **B-light** keep handlers in `work_orders.py`, add
   `/api/reports/cost-reports/*` aliases that call the same functions;
   `CostReports.tsx:105-122` swaps the prefix. **B-full** would move
   the 5 handlers into `reports.py`, but that pulls tenant_db cost
   methods (`cost_by_vehicle`, etc.) into a file that today doesn't
   import them. Recommend B-light.
4. Add Cost Reports entry to `routeRegistry.ts`.
5. Leave `/api/user/scheduled-reports` under `/user/*` — it's per-user
   state (frequency/channels/hour) and the URL is correct; document
   the deliberate split.

**Files:** `router.tsx`, 3 nav files, `CostReports.tsx`, `reports.py`
(aliases), `routeRegistry.ts`.

**Risk:** low — only known consumer of `/work-orders/reports/*` is
`CostReports.tsx` (bot/scheduler don't call those endpoints).

**Doesn't solve:** sidebar flatness, shared chrome, cross-page nav.

### Option C — Full Reports shell (L, ~2-3d)

1. New `pages/reports/ReportsLayout.tsx` with shared `<PageHeader>` +
   `<ReportsSubNav>` + `<Outlet/>`.
2. Refactor `router.tsx:166-195` into nested:
   ```tsx
   <Route path="reports" element={L(<ReportsLayout />)}>
     <Route index               element={…<Reports /></P>} />
     <Route path="risk-summary" element={…<RiskSummary /></P>} />
     <Route path="cost-reports" element={…<CostReports /></P>} />
     <Route path="scheduled-reports" element={…<ScheduledReports /></P>} />
   </Route>
   ```
   Open question: keep faults/fuel/health/efficiency as in-page tabs
   (current `Reports.tsx:245-257`) or promote to routes? Recommend
   keep — avoids 4× URL inflation.
3. Each child page drops its own `<PageHeader>`; layout owns chrome.
   Actions slot via `useOutletContext` or React Context.
4. Sub-nav filters tabs by persona using the same flag tags pattern as
   today's `Reports.tsx:130-151`; landing redirects to first allowed
   tab.
5. Sidebar collapses to one Reports entry per persona (or
   `permission: [...all child flags]` for union visibility).
6. Accounting's `/costs/fuel`, `/costs/cpm`, `/payroll` move out of
   `nav.reports_group` into a new `nav.costs_group` so the label stops
   overloading (resolves H8).
7. Bookmark redirects: keep old standalone routes as `<Navigate>` —
   Telegram bot uses `/reports/risk-summary` (`bot/reports.py:161`).

**Files:** new ReportsLayout + ReportsSubNav, `router.tsx`, 4 reports
pages (strip PageHeader), 6 nav files, `routeRegistry.ts`.

**Risk:** largest UX delta; per-persona sub-tab visibility;
`useSearchParams` collision between parent layout and children that
already use `?subject_type=…` (RiskSummary) and `?tab=…` (Reports);
migration redirects.

**Doesn't solve:** "5 places define the report list"
(reports-module-audit §3.6); cross-channel data drift (§3.3);
`data/reports.ts` parity discipline.

## 4. Recommendation

Sequence: **B → C; skip A unless C is rejected.**

1. Ship **Option B** first. ~6h fixes the only broken hierarchy claim
   (Cost Reports outside `/reports/*`) and establishes the canonical
   URL prefix for any later layout.
2. Defer **Option A** — partially obsoleted by C.
3. Treat **Option C** as Phase 2 if the team agrees Reports should
   feel like a module. Run after the permissions matrix settles
   (cross-ref permissions-feature-matrix.md).
4. If C is rejected, A becomes the visual half: A+B gets ~70% of the
   hierarchical feel for ~50% of C's effort.

## 5. Carryover (not in scope)

- reports-module-audit A1 (bot redirect paths don't exist).
- reports-module-audit A2 (cross-channel data drift, API vs bot).
- H11 (rename `data/reports.ts` → `data/scheduled_reports.ts`) — do
  alongside reports-module-audit Tier 3.8 ("Finish 'Scheduled Reports'
  rename").
- H4/H12 (per-page chrome divergence) — only resolved by Option C.
