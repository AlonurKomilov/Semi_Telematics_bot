# Permissions Feature Matrix — 4truck Dashboard

> ⚠️ **HISTORICAL SNAPSHOT (2026-06-02) — superseded, kept for provenance.**
> This was a one-time audit that *drove* the next round of permissions work; it
> is **not** a living document and its file:line references are stale (paths
> like `capabilities/iam/permissions.py` and `pages/admin/RolePermissions.tsx`
> no longer exist; flags were renamed `_own` → `_vehicle`; several "suggested
> relabels" below have since shipped — `can_cost_reports` was split out,
> `can_rolling_stopped` was removed, and Alerts/AI/Reports became services).
>
> **For current state, read the living SSOTs instead:**
> - [`../FEATURES.md`](../FEATURES.md) — the feature taxonomy (toggleable surfaces)
> - [`../SERVICES.md`](../SERVICES.md) — the always-on system services (Alerts / AI / Reports)
> - the code: `capabilities/permissions/roles.py` (`FeatureSet` · `ROLE_PERMISSIONS` · `TOOL_PERMISSIONS` · `derive_service_perms`) and `interfaces/dashboard/src/features/permissions/Permissions.tsx` (`PERM_GROUPS`).
>
> Everything below is preserved as the original 2026-06-02 evidence base.

> **As of 2026-06-02.** This is the evidence base for the next round of
> permissions-module work. Every row is a concrete feature surface
> traceable to a file:line. All paths are relative to the repo root
> (`/home/abcdev/projects/Semi_Telematics_bot`).

Sources scanned:

- `capabilities/iam/permissions.py` — `FeatureSet` (line 63), `ROLE_PERMISSIONS` (line 129), `_FEATURE_LABELS` (line 565), `TOOL_PERMISSIONS` (line 787)
- `interfaces/dashboard/src/pages/admin/RolePermissions.tsx` — `PERM_GROUPS` (line 87)
- `interfaces/dashboard/src/router.tsx` — all `<Route>` entries (line 138-218)
- `interfaces/dashboard/src/shells/nav/{default,fleet,safety,dispatch,hr,accounting}Nav.ts`
- `interfaces/api/routes/*.py` — every `require_permission(...)` / `require_permission_any(...)` (260 hits)
- `capabilities/ai/tools/*.py` — every `@register_tool`
- `interfaces/bot/*.py` — `can(user.role, ...)` checks

Conventions used in the matrix:

- **Roles ON by default** excludes Owner/Admin (always full). Listed roles are working roles only. `(all)` = Fleet+Safety+Dispatch+HR+Accounting+Driver.
- **Sidebar(s)** = personas whose `*Nav.ts` config lists this entry by `path`. The nav is a *visibility* filter — the route guard is still the SSOT for access.
- **Group on RolePermissions page** = which `PERM_GROUPS` entry in `RolePermissions.tsx` contains the flag.

---

## 1. Master Matrix (sorted by Group → Flag)

### Group: Administration

| # | Feature (human label) | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | Send Invites (POST/DELETE invite tokens) | api-route | `interfaces/api/routes/admin.py:524,566` | – | `can_invite` | HR | Administration | Owner/Admin/HR only |
| 2 | Invites page | dashboard-page | `router.tsx:197` (`/admin/invites`) | Owner, HR | `can_invite` | HR | Administration | – |
| 3 | Manage Billing & Subscription (billing routes) | api-route | `interfaces/api/routes/billing.py:30` (module-level dep) | – | `can_manage_billing` | Accounting | Administration | – |
| 4 | Billing page | dashboard-page | `router.tsx:213` (`/admin/billing`) | Owner, Accounting | `can_manage_billing` | Accounting | Administration | – |
| 5 | Account Settings (working-hours, telegram bot, scorecard rules, perms, storage, audit settings) | api-route | `admin.py:762,800,838,849,874,894,920,…,1752` (40+ endpoints), `permissions.py:72-315`, `storage.py:190,309,361,387,442,639`, `safety.py:947-991`, `ai.py:311` | – | `can_manage_account` | (none) | Administration | **Overloaded** — sole gate for Storage, Role Permissions, Scorecard Rules, Working Hours, account-level Settings, AI persona config, pillar caps, score rules. Owner/Admin only by default. |
| 6 | Working Hours page | dashboard-page | `router.tsx:196` (`/admin/work-hours`) | Owner | `can_manage_account` | (none) | Administration | – |
| 7 | Settings page | dashboard-page | `router.tsx:198` (`/admin/settings`) | Owner | `can_manage_account` | (none) | Administration | – |
| 8 | Storage page | dashboard-page | `router.tsx:202` (`/admin/storage`) | Owner | `can_manage_account` | (none) | Administration | – |
| 9 | Role Permissions page | dashboard-page | `router.tsx:203` (`/admin/permissions`) | Owner | `can_manage_account` | (none) | Administration | – |
| 10 | Scorecard Rules page | dashboard-page | `router.tsx:204` (`/admin/scorecard-rules`) | Owner | `can_manage_account` | (none) | Administration | – |
| 11 | Manage Companies (CRUD) | api-route | `admin.py:608,633,684,724` | – | `can_manage_companies` | (none) | Administration | Owner only by default (Admin has it FALSE in `ROLE_PERMISSIONS[ADMIN]`, line 161). |
| 12 | Companies page | dashboard-page | `router.tsx:194` (`/admin/companies`) | Owner | `can_manage_companies` | (none) | Administration | – |
| 13 | Manage Users / list / setrole / remove / audit-log | api-route | `admin.py:32,111,169,209,245,281,308,351,385,745` | – | `can_manage_users` | HR | Administration | **Overloaded** — also gates the Audit Log endpoint (`admin.py:745`) and the Users management table. Description on RolePermissions calls this out (line 181). |
| 14 | Team Management (Users) page | dashboard-page | `router.tsx:193` (`/admin/users`) | Owner | `can_manage_users` | HR | Administration | – |
| 15 | Audit Log page | dashboard-page | `router.tsx:195` (`/admin/audit`) | Owner | `can_manage_users` | HR | Administration | Sidebar entry: Owner only (defaultNav line 114). HR has the flag but no sidebar entry. |

### Group: Costs

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 16 | Cost per Mile page | dashboard-page | `router.tsx:168` (`/costs/cpm`) | Fleet, Accounting | `can_cost_per_mile` | Fleet, Accounting | Costs | – |
| 17 | Cost per Mile API | api-route | `interfaces/api/routes/costs.py:77` | – | `can_cost_per_mile` | Fleet, Accounting | Costs | – |
| 18 | Fuel Costs page | dashboard-page | `router.tsx:167` (`/costs/fuel`) | Dispatch, Accounting | `can_fuel_cost` | Fleet, Accounting | Costs | Default is *Fleet + Accounting* but **only Dispatch + Accounting expose it in their sidebars**; Fleet's sidebar omits it (fleetNav line 62 comment: "intentionally omitted"). |
| 19 | Fuel Cost API | api-route | `costs.py:27,43,64` | – | `can_fuel_cost` | Fleet, Accounting | Costs | – |

### Group: Fleet Operations

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 20 | Geofences page | dashboard-page | `router.tsx:150` (`/geofences`) | Fleet, Dispatch | `can_geofence_all`, `can_geofence_own` | Fleet, Safety, Dispatch, HR, Driver(own) | Fleet Operations | – |
| 21 | Geofences API (list/get) | api-route | `interfaces/api/routes/geofences.py:44,218` | – | `can_geofence_all` ∨ `can_geofence_own` | same | Fleet Operations | – |
| 22 | Geofences CRUD (create/update/delete/etc.) | api-route | `geofences.py:150,199,226,234` | – | `can_geofence_all` | Fleet, Safety, Dispatch, HR | Fleet Operations | Asymmetric: writes require *all* even when caller has *own*. |
| 23 | PTI Inspections page | dashboard-page | `router.tsx:176` (`/inspections`) | Fleet | `can_inspections_all` | Fleet, Safety, Dispatch, HR | Fleet Operations | Sidebar: Fleet only (others have flag but no sidebar entry). |
| 24 | Inspections API (driver submit) | api-route | `interfaces/api/routes/inspections.py:424-1317` (mix) | – | `can_inspections_all` ∨ `can_inspections_own` | (all except Accounting) | Fleet Operations | – |
| 25 | Inspections admin (templates, review) | api-route | `inspections.py:960-1390` | – | `can_inspections_all` | Fleet, Safety, Dispatch, HR | Fleet Operations | – |
| 26 | Live Map page | dashboard-page | `router.tsx:146` (`/live-map`) | Owner, Fleet, Safety, Dispatch, HR | `can_location_map`, `can_location_own` | (all) | Fleet Operations | – |
| 27 | Live Map / vehicle locations API | api-route | `interfaces/api/routes/maps.py:28,106` | – | `can_location_map` ∨ `can_location_own` | (all) | Fleet Operations | – |
| 28 | Maintenance page | dashboard-page | `router.tsx:171` (`/maintenance`) | Fleet | `can_maintenance_all`, `can_maintenance_own` | Fleet, Safety, Driver(own), Accounting(all) | Fleet Operations | **Overloaded flag** — `can_maintenance_all` also gates Cost Reports + Work Order admin (route 182, 187). RolePermissions label "Maintenance & Work Orders" partially names it; the *Cost Reports* gating is undocumented in the label. Comment in `permissions.py:289-294` explicitly admits Accounting has `can_maintenance_all` purely for Cost Reports. |
| 29 | Maintenance API | api-route | `interfaces/api/routes/maintenance.py:318-1235` (13 endpoints) | – | `can_maintenance_all` | Fleet, Safety, Accounting | Fleet Operations | All writes require `can_maintenance_all`; `can_maintenance_own` has no API surface (only used on the dashboard route guard). |
| 30 | Work Orders page (list / detail) | dashboard-page | `router.tsx:181,183` (`/work-orders`, `/work-orders/:id`) | Fleet | `can_maintenance_all`, `can_maintenance_own` | same as #28 | Fleet Operations | – |
| 31 | Work Orders new (create form) | dashboard-page | `router.tsx:182` (`/work-orders/new`) | – | `can_maintenance_all` | Fleet, Safety, Accounting | Fleet Operations | All-only. |
| 32 | Cost Reports page | dashboard-page | `router.tsx:187` (`/cost-reports`) | Owner, Fleet, Accounting | `can_maintenance_all` | Fleet, Safety, Accounting | Fleet Operations | **Mis-grouped on RolePermissions** — Cost Reports lives under *Fleet Operations* group via `can_maintenance_all`, despite being a financial surface. Sidebar entry on Owner + Accounting persona suggests it should live in Costs. |
| 33 | Work Orders API (CRUD) | api-route | `interfaces/api/routes/work_orders.py:184-623` (12 endpoints) | – | `can_maintenance_all` | Fleet, Safety, Accounting | Fleet Operations | – |
| 34 | Manage POI Layers (CRUD) | api-route | `interfaces/api/routes/pois.py:470-910` (8 endpoints) | – | `can_manage_poi_layers` | Fleet | Fleet Operations | Owner/Admin/Fleet only. No dashboard page-level surface for the *list*; it's a Live-Map sub-UI. |
| 35 | List POIs (read) | api-route | `pois.py:253,459` | – | `can_location_map` ∨ `can_location_own` | (all) | Fleet Operations | – |
| 36 | Routes page | dashboard-page | `router.tsx:149` (`/routes`) | Dispatch | `can_route_all`, `can_route_own` | Fleet, Safety, Dispatch, Driver(own) | Fleet Operations | Strict-binding intent: only Dispatch sidebar exposes routes (other personas have the flag but no sidebar). |
| 37 | Routes API | api-route | `interfaces/api/routes/routes.py:21,92,121,128` | – | `can_route_all` ∨ `can_route_own` | same | Fleet Operations | – |
| 38 | Vehicle Movement Status (AI tool `get_rolling_stopped`) | ai-tool | `capabilities/ai/tools/vehicle.py:92` + `permissions.py:806` | – | `can_rolling_stopped` | Dispatch | Fleet Operations | **Overloaded label** — labelled "Vehicle Movement Status" on RolePermissions but its only enforcement point is *one AI tool*. No dashboard page, no API route. The flag was originally documented as gating "rolling/stopped notifications" (line 86 doc-string) but the notification feature is not wired anywhere I can find. |
| 39 | Vehicles list page | dashboard-page | `router.tsx:147` (`/vehicles`) | Owner, Fleet, Safety, Dispatch, HR, Accounting | `can_vehicle_all`, `can_vehicle_own` | (all) | Fleet Operations | – |
| 40 | Vehicle Detail page | dashboard-page | `router.tsx:148` (`/vehicles/:name`) | – | `can_vehicle_all`, `can_vehicle_own` | (all) | Fleet Operations | – |
| 41 | Vehicle list / search API | api-route | `interfaces/api/routes/vehicles.py:222,321,388,413,468` | – | `can_faults` ∨ `can_vehicle_all` ∨ `can_vehicle_own` (mixed) | – | Fleet Operations | Inconsistent: some endpoints gate on `can_faults` (e.g. `:222` = fault summary), others on `can_health`, some accept all three flags. |
| 42 | Vehicle account stats (fleet snapshot) | api-route | `fleet.py:321` | – | `can_vehicle_all` | Fleet, Safety, Dispatch, HR, Accounting | Fleet Operations | – |

### Group: Reports

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 43 | Critical Faults Report | (no direct surface) | label only in RolePermissions:132 | – | `can_critical` | Fleet, Safety | Reports | **Orphan** — flag exists in `FeatureSet:68` + `ROLE_PERMISSIONS`, has a label, but is referenced by **zero** API routes / AI tools / dashboard pages / bot handlers. Dead. |
| 44 | Auto-Report Subscriptions | api-route + bot | `interfaces/bot/auto_reports.py:62,96` | – (Subscriptions page has `permission: null`) | `can_digest` | (all except non-Driver vary) | Reports | Bot-only enforcement. Dashboard `/reports/subscriptions` is **ungated** (`router.tsx:163`). |
| 45 | Efficiency Report | api-route | `interfaces/api/routes/reports.py:151,202` | – | `can_efficiency` ∨ `can_vehicle_all` | Fleet, Accounting | Reports | – |
| 46 | Faults Report (PDF) | api-route | `reports.py:45` + `fleet.py:87,104,366,373` | – | `can_faults` | Fleet, Safety | Reports | **Overloaded flag** — `can_faults` gates: Faults Report API, Cameras page (router.tsx:155), AI Chat (router.tsx:158), AI Summary (router.tsx:159), Reports landing page (router.tsx:162). RolePermissions:128-131 documents this. |
| 47 | Reports landing page | dashboard-page | `router.tsx:162` (`/reports`) | (all personas) | `can_faults` | Fleet, Safety | Reports | Driver / Dispatch / Accounting see "Reports" in their sidebar but `can_faults` is FALSE for them → 403 on click. |
| 48 | Cameras page | dashboard-page | `router.tsx:155` (`/cameras`) | Fleet, Safety, Dispatch | `can_faults` | Fleet, Safety | Reports | **Sidebar/default mismatch** — Dispatch persona shows Cameras in sidebar (dispatchNav line 56) but Dispatcher `can_faults=False` → 403. |
| 49 | AI Chat page | dashboard-page | `router.tsx:158` (`/ai/chat`) | (all — via overview/header) | `can_faults` | Fleet, Safety | Reports | Sidebar exposes "AI Assistant" everywhere with perm `['can_vehicle_all', 'can_vehicle_own']` but the *route guard* uses `can_faults`. **Inconsistency**: a driver sees the sidebar link (their own vehicle perm) but the route returns 403. |
| 50 | AI Summary page | dashboard-page | `router.tsx:159` (`/ai/summary`) | – | `can_faults` | Fleet, Safety | Reports | – |
| 51 | AI Chat conversation API | api-route | `interfaces/api/routes/ai.py:198,234` | – | `can_faults` ∨ `can_vehicle_all` (`:198`); `can_faults` only (`:234`) | – | Reports | – |
| 52 | Fuel Report | api-route | `reports.py:76,121` | – | `can_fuel` | Fleet, Dispatch, Accounting | Reports | – |
| 53 | Health Report | api-route | `reports.py:130` + `fleet.py:73` (legacy) | – | `can_health` | Fleet, Safety | Reports | – |
| 54 | Risk Summary page | dashboard-page | `router.tsx:164` (`/reports/risk-summary`) | Owner, Fleet, Safety, HR | `can_risk_report_all`, `can_risk_report_own` | Safety, HR, Fleet(own), Driver(own) | Reports | – |
| 55 | Risk Summary API | api-route | `reports.py:264,354` | – | `can_risk_report_all` ∨ `can_risk_report_own` | same | Reports | – |

### Group: Safety & Compliance

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 56 | Alerts page | dashboard-page | `router.tsx:152` (`/alerts`) | Owner, Fleet, Safety, Dispatch, HR | `can_alerts_all`, `can_alerts_own` | (all) | Safety & Compliance | – |
| 57 | Alerts API (list, ack, mute, etc.) | api-route | `interfaces/api/routes/alerts.py:161-549` (11 endpoints) | – | `can_alerts_all` ∨ `can_alerts_own` | same | Safety & Compliance | – |
| 58 | Alert health-check admin (re-escalation) | api-route | `admin.py:1752` | – | `can_alerts_all` | Fleet, Safety, Dispatch, HR | Safety & Compliance | – |
| 59 | Parking page | dashboard-page | `router.tsx:151` (`/parking`) | Fleet, Safety, Dispatch | `can_alerts_all`, `can_alerts_own` | same as Alerts | Safety & Compliance | – |
| 60 | Parking API | api-route | `parking.py:19,43,66,87,107,133` | – | `can_alerts_all` ∨ `can_alerts_own` ∨ `can_vehicle_all` | same | Safety & Compliance | – |
| 61 | Safety Events page | dashboard-page | `router.tsx:154` (`/safety-events`) | Safety, HR | `can_events_all`, `can_events_own` | Fleet, Safety, Dispatch, HR, Driver(own) | Safety & Compliance | – |
| 62 | Safety Events API | api-route | `safety.py:1071,1169,1261,1315` | – | `can_events_all` ∨ `can_events_own` | same | Safety & Compliance | – |
| 63 | Scorecards page | dashboard-page | `router.tsx:153` (`/scorecards`) | Fleet, Safety, Dispatch, HR | `can_scorecard_all`, `can_scorecard_own` | Fleet, Safety, Dispatch, HR, Driver(own) | Safety & Compliance | – |
| 64 | Scorecards API | api-route | `safety.py:36-1035` (10 endpoints) | – | `can_scorecard_all` ∨ `can_scorecard_own` | same | Safety & Compliance | – |
| 65 | Camera check (AI tool) | ai-tool | `capabilities/ai/tools/camera.py:12` + `permissions.py:804` | – | `can_vehicle_all` | (all except Driver) | Safety & Compliance | – |

### Group: Workforce

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 66 | Coaching page | dashboard-page | `router.tsx:215` (`/coaching`) | Safety, HR | `can_coaching_admin` | Fleet, Safety, HR | Workforce | Sidebar visible to Safety + HR but Fleet has the flag too (no sidebar entry). |
| 67 | Coaching API (admin) | api-route | `interfaces/api/routes/coaching.py:88-227` (9 endpoints) | – | `can_coaching_admin` | Fleet, Safety, HR | Workforce | – |
| 68 | Coaching driver view (acknowledge, listing) | api-route | `coaching.py:73,271,292` | – | `can_coaching_admin` ∨ `can_coaching_view_own` | + Driver(own) | Workforce | – |
| 69 | View Own Coaching | api-route (driver miniapp) | `coaching.py:271,292` (any-perm) | – | `can_coaching_view_own` | Driver | Workforce | – |
| 70 | Drivers / Workforce page | dashboard-page | `router.tsx:216` (`/workforce/drivers`) | Fleet, Safety, Dispatch, HR | `can_manage_driver_docs` | Fleet, Safety, HR | Workforce | Dispatch sidebar shows it (dispatchNav line 63) but Dispatcher has `can_manage_driver_docs=False` → 403. **Sidebar/default mismatch.** |
| 71 | Drivers admin API (CRUD, doc upload) | api-route | `interfaces/api/routes/drivers.py:216,231,397,420,438,482,642` | – | `can_manage_driver_docs` | Fleet, Safety, HR | Workforce | – |
| 72 | Drivers list (mixed-scope) | api-route | `drivers.py:284,312,336,377,459,601,676` | – | `can_manage_driver_docs` ∨ `can_driver_docs_own` | + Driver(own) | Workforce | – |
| 73 | View Own Driver Documents | api-route (miniapp) | `drivers.py:284,...` | – | `can_driver_docs_own` | Driver | Workforce | No dashboard page surface — driver-only via Telegram MiniApp. |
| 74 | Payroll page | dashboard-page | `router.tsx:214` (`/payroll`) | Accounting | `can_payroll_admin` | Accounting | Workforce | – |
| 75 | Payroll admin API | api-route | `interfaces/api/routes/payroll.py:76-235` (11 endpoints) | – | `can_payroll_admin` | Accounting | Workforce | – |
| 76 | Payroll self-service (own paystubs) | api-route | `payroll.py:278` | – | `can_payroll_admin` ∨ `can_payroll_view_own` | + Driver | Workforce | No dashboard page — Driver MiniApp / bot only. |
| 77 | View Own Paystubs | bot/api | `payroll.py:278`, `interfaces/bot/payroll.py:43-44` | – | `can_payroll_view_own` | Driver | Workforce | – |

### Group: (none — flag is in PERM_GROUPS but no enforcement surface)

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| — | (none — see Orphan Flags below for can_critical) | | | | | | | |

### Group: (orphan — surface has no flag)

| # | Feature | Source surface | Where | Sidebar(s) | Permission flag | Roles ON by default | Group | Notes |
|---|---|---|---|---|---|---|---|---|
| 78 | Overview page | dashboard-page | `router.tsx:140` (`/`) | (every persona) | `(none)` | – | – | Index route. Intentional — no permission means "everyone in the account." |
| 79 | Subscriptions page (Reports) | dashboard-page | `router.tsx:163` (`/reports/subscriptions`) | (every persona) | `(none)` | – | – | Permission: `null` in every nav file. The bot enforces `can_digest` on auto-report subscriptions (`interfaces/bot/auto_reports.py:62`) but the dashboard route is open. **Defect**: a driver can hit the page even when `can_digest=True` is intended to be the gate. |
| 80 | Knowledge Base page | dashboard-page | `router.tsx:190` (`/knowledge`) | (every persona) | `(none)` | – | – | Intentional — uses internal `_can_view_article` ACL inside the page, not a sidebar-style flag. |
| 81 | Profile page | dashboard-page | `router.tsx:201` (`/profile`) | – (AvatarMenu only) | `(none)` | – | – | Intentional — personal preferences. |
| 82 | Inspection-template redirect | dashboard-page | `router.tsx:210` (`/admin/inspection-template`) | – | `(none)` | – | – | 301 to `/inspections?tab=template`. Inspections page itself is `can_inspections_all`. |
| 83 | AI Assistant sidebar link | sidebar-entry | every `*Nav.ts` (e.g. defaultNav line 51) | (every persona) | `can_vehicle_all` ∨ `can_vehicle_own` | (all) | – | **Inconsistency** — sidebar shows the link to every persona that has *any* vehicle access, but the underlying `/ai/chat` route gates on `can_faults` only. Drivers, Dispatch, HR, Accounting all see the link → 403. |

---

## 2. Anomalies

### 2.1 Orphan flags (in `FeatureSet`, no enforcement surface)

- **`can_critical`** — Defined `FeatureSet:68`, set to True for Owner/Admin/Fleet/Safety, labelled "Critical Faults Report" on RolePermissions:132, advertised in `_FEATURE_LABELS:566`. **Zero** call sites in `interfaces/api/routes/`, `capabilities/ai/tools/`, `interfaces/bot/`, or `interfaces/dashboard/`. Dead flag.

That is the only true orphan flag. Every other flag is referenced by at least one surface.

### 2.2 Orphan features (sidebar / route entries with no flag)

| Entry | File:line | Defect? |
|---|---|---|
| `/reports/subscriptions` route | `router.tsx:163` | **Yes** — no `<P perm>` wrapper, gate exists only in bot (`auto_reports.py:62` checks `can_digest`). |
| `/` (Overview) | `router.tsx:140` | Intentional. |
| `/knowledge` | `router.tsx:190` | Intentional — page-internal ACL. |
| `/profile` | `router.tsx:201` | Intentional — own profile. |
| "Knowledge Base" sidebar links in every persona | various | Intentional — `permission: null`. |
| "Overview" sidebar links | various | Intentional. |
| "Reports" sidebar link (defaultNav, fleetNav, safetyNav, dispatchNav, hrNav, accountingNav) | various | **Latent defect** — sidebar has `permission: null` but the *route* is gated on `can_faults`. Roles without `can_faults` (Driver, Dispatch, HR, Accounting) see the entry and 403 on click. |
| "Subscriptions" sidebar link | various | Same shape as above — `permission: null`, route ungated, bot-only enforcement. |

### 2.3 Sidebar ⇄ default mismatches

Per persona, items in their `*Nav.ts` whose flag is **OFF** in `ROLE_PERMISSIONS[<role>]` (would 403 on click for the canonical role behind that subdomain):

- **Dispatch sidebar / Dispatcher defaults:**
  - `nav.cameras` (`/cameras`, `can_faults`) — Dispatcher `can_faults=False` (`permissions.py:234`). dispatchNav:56.
  - `nav.drivers` (`/workforce/drivers`, `can_manage_driver_docs`) — Dispatcher `can_manage_driver_docs=False` (no entry in DISPATCHER FeatureSet → defaults False). dispatchNav:63.
- **HR sidebar / HR defaults:**
  - `nav.invites` (`/admin/invites`, `can_invite`) — HR has `can_invite=True` (line 262) so OK; included for completeness.
- **Accounting sidebar / Accounting defaults:**
  - `nav.fuel_costs`, `nav.cost_per_mile`, `nav.cost_reports`, `nav.payroll`, `nav.billing`, `nav.vehicles` — all matched. No mismatch.
- **Safety sidebar / Safety defaults:**
  - `nav.cameras` (`can_faults`) — Safety has `can_faults=True` (line 207). OK.
- **Fleet sidebar / Fleet defaults:**
  - `nav.cameras` (`can_faults`) — Fleet has `can_faults=True` (line 181). OK.
- **Default (Owner/Admin) sidebar:**
  - All present-and-true.

Roles with a flag ON but **no sidebar entry exposes it** anywhere (orphan defaults):

- **`can_critical`** — flag is ON for Fleet+Safety but no UI exposes it (see Orphan Flags above).
- **`can_efficiency`** — ON for Fleet+Accounting, has Reports surface (`reports.py:151`) but no dedicated sidebar entry. The label "Efficiency Report" lives only on RolePermissions:134.
- **`can_fuel`** — ON for Fleet+Dispatch+Accounting; surfaces only via the Reports landing page (gated by `can_faults`, not `can_fuel`). Fuel-specific report API exists (`reports.py:76`) but it's not discoverable from any persona's sidebar.
- **`can_health`** — ON for Fleet+Safety; same shape as `can_fuel`. Report API exists; no sidebar nav.
- **`can_inspections_own`** — ON for Driver only; no dashboard surface (driver uses MiniApp/Telegram).
- **`can_driver_docs_own`** — ON for Driver only; no dashboard surface.
- **`can_payroll_view_own`**, **`can_coaching_view_own`** — same pattern.
- **`can_route_own`**, **`can_scorecard_own`**, **`can_events_own`**, **`can_maintenance_own`**, **`can_geofence_own`**, **`can_alerts_own`**, **`can_location_own`**, **`can_vehicle_own`**, **`can_risk_report_own`** — driver/own-scope flags; they pair with their `_all` counterparts on dashboard nav (the route checks accept either).

### 2.4 Overloaded flags

Confirmed (already known, listed for completeness): `can_faults`, `can_manage_users`, `can_manage_account`, `can_maintenance_all`, `can_rolling_stopped`.

**New overloads discovered:**

1. **`can_alerts_all` / `can_alerts_own`** — Also gates the Parking page (`router.tsx:151`, `parking.py`). RolePermissions label is "Alerts" only; the Parking surface is undocumented. Anyone toggling Alerts off for a role unwittingly disables Parking too.
2. **`can_vehicle_all`** — In addition to the Vehicles list, gates several AI tools (`check_vehicle_camera`, `get_weather`, `get_account_stats`, `get_drivers_list`, `search_vehicles`) and the dashboard sidebar's "AI Assistant" visibility. Label "Vehicles" implies a single page.
3. **`can_geofence_all`** — Gates *write* operations on Geofences (`geofences.py:150,199,226,234`); `can_geofence_own` only allows read. The "Geofences" label doesn't communicate that the write/read split is on the all-vs-own axis.
4. **`can_inspections_all`** — Gates two distinct dashboard surfaces: the Inspections review queue AND the inspection-template editor (router.tsx:210 redirect → `/inspections?tab=template`). RolePermissions label "PTI Inspections" doesn't mention the template authoring scope.
5. **`can_maintenance_all`** — In addition to Maintenance pages, gates **Work Orders create form**, **Cost Reports**, and the *entire* Work Orders API. RolePermissions label "Maintenance & Work Orders" mentions Work Orders but not Cost Reports.

### 2.5 Confusing labels (recommendations)

| Current label | Flag | Confusion | Suggested rename |
|---|---|---|---|
| "Vehicle Movement Status" | `can_rolling_stopped` | Sounds like a fleet feature; only enforces an AI tool. Doc-string promises rolling/stopped *notifications* but no notification surface exists. | "AI: Rolling/Stopped Lookup" — and remove from Fleet Operations group until a real feature surface exists. |
| "Alerts" | `can_alerts_all` / `_own` | Also gates Parking. | "Alerts & Parking" or split the Parking page off onto its own flag. |
| "Faults Report" | `can_faults` | Already has a description on the page; even so the breadth is large (Cameras, AI Chat, AI Summary, Reports landing). | Keep but consider splitting AI access (`can_use_ai`) off — drivers should be able to use AI on their own truck without inheriting Faults Report access. |
| "Maintenance & Work Orders" | `can_maintenance_all` | Cost Reports also gated. | "Maintenance, Work Orders & Cost Reports" — or split Cost Reports onto its own flag (`can_cost_reports`) per the existing TODO at `permissions.py:289-294`. |
| "Vehicles" | `can_vehicle_all` | Also gates several AI tools and the "AI Assistant" sidebar visibility. | "Vehicles & Fleet Stats" — keep AI tools grouped under their own future `can_use_ai` flag. |
| "Geofences" | `can_geofence_all` | All-vs-own split silently encodes read-vs-write asymmetry. | Add description: "Granting 'All' is required to create/edit/delete; 'Own' is read-only." |
| "Critical Faults Report" | `can_critical` | Reads as a live feature; it's a dead flag. | Either delete the flag or wire it to the critical-fault email/PDF that the doc-string at `permissions.py:68` references. |

---

## 3. Quick-reference counts

- **Distinct rows in matrix:** 83 (Administration 15, Costs 4, Fleet Operations 23, Reports 13, Safety & Compliance 10, Workforce 12, Ungrouped/orphan 6).
- **FeatureSet flags total:** 40 (counting `_all` and `_own` separately).
- **Flags with at least one enforcement surface:** 39 (all except `can_critical`).
- **Flags in PERM_GROUPS:** 29 user-visible toggles (some scoped pair into one row on the page).
- **Orphan flags:** 1 (`can_critical`).
- **Ungated dashboard routes:** 5 (Overview, Subscriptions, Knowledge, Profile, inspection-template-redirect) + Reports/Subscriptions ungated despite being a feature surface.
- **Sidebar entries that 403 against their role's defaults:** 2 confirmed (Dispatch → Cameras, Dispatch → Drivers).
- **Sidebar/route flag mismatches:** AI Assistant sidebar (`can_vehicle_*`) vs `/ai/chat` route (`can_faults`); Reports sidebar (`null`) vs `/reports` route (`can_faults`); Subscriptions sidebar (`null`) vs route (`null`, bot enforces `can_digest`).
