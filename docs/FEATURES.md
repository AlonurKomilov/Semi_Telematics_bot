# Feature taxonomy — the 4-tier model

Decided 2026-06-10; retiered 2026-07-30 (see "Tier definitions").  This is
the SSOT for how we classify product surface.  The catalog
(`interfaces/dashboard/src/config/featureCatalog.ts`) encodes the tier per
feature; the Permissions page bands its rows the same way.

> **Alerts, the AI assistant, and Reports are NOT features** — they are
> always-on **system services** (every role has them; access is *derived*, not
> toggled). They have a different architecture and their own SSOT:
> [`SERVICES.md`](SERVICES.md). This doc covers only the toggleable feature
> taxonomy; it points to SERVICES.md for the service layer and never duplicates
> it.

## The axes (each answers ONE question)

| Axis | Question | Mechanism |
|---|---|---|
| **Tier** (Personal / Shared / Role / Administration) | who is this for? | catalog `tier` |
| **Module** | is the department switched on for the account? | the Departments strip on the Permissions page (`capabilities/permissions/modules.py`) |
| **Permission** | may this role open it? | the Permissions page's per-role grid (`can_*` flags) |
| **Data scope** | whose data do they see? (All / Company / Vehicle) | Team Management, per-user |
| **Backend pattern** (hub / entity / workflow) | how is it implemented? | not a tier — see below |

"Admin" is **not** the permission axis' name either — `can_manage_*` gates
are just permissions, and the governance features they gate now have their
own tier (Administration).

A **feature** has its own identity and lifecycle. A **component** is a part of
one (a config panel, a preferences page, a viewer, a sub-permission). Tiers
classify features only; components inherit their parent's tier.

## Tier definitions

Every tier answers ONE question: **who is this for?**

1. **Personal** — the individual, *about themselves*. The test is whose data
   it shows, not who it is assembled for: Profile (name / language / timezone
   / DND) and the own-record surfaces (`can_driver_docs_own`,
   `can_driver_pay_view_own`, `can_coaching_view_own`, `can_loads_own`,
   `can_risk_report_own`).  Each own-record surface is the twin of a
   department's admin one — Coaching (HR) ↔ Own Coaching, Driver Pay
   (Accounting) ↔ Own Paystubs, Risk Summary (Safety) ↔ Own Risk Summary.
   Naming the pattern is what stops the next pair being invented ad hoc.
   NOTE Profile lives in `src/pages/` with the app-shell pages: placement is
   not taxonomy (the same principle that kept Working Hours' classification
   when its router moved).
2. **Shared** — one entity/view useful to several departments. Usually the
   page **composes different components per role** (persona layouts);
   Overview is the purest case, and Knowledge Base the case where every
   department reads the same thing. A Shared feature may have NO permission
   at all (Overview, Knowledge Base) — tier and permission are separate
   axes on purpose.
3. **Role** — a single department's workflow; surfaced only when that
   department's module is on. A Role feature MAY list several modules
   (e.g. Safety Events: safety + hr) — tier describes its single-workflow
   nature, modules describe who surfaces it.
4. **Administration** — governing the account itself: Permissions,
   Integrations, Storage, General settings, Team Management, Send Invites,
   Manage Companies, Working Hours. Each is a FEATURE with its own home
   (a folder + router under `features/settings/` or `capabilities/`) and its
   own catalog entry; the Settings page groups several in the NAV, and
   grouping was never ownership.

**"System" was retired 2026-07-30.** Once Alerts / AI / Reports left to
become always-on **services** ([`SERVICES.md`](SERVICES.md)), the tier held
only governance pages (now Administration) plus Overview / Knowledge Base
(Shared) and Profile (Personal) — i.e. it had become a synonym for
"miscellaneous account-wide". Hub *machinery* still carries no tier; the
three service entries in the catalog carry `tier: 'service'`, which marks
them as outside the axis rather than pretending they sit on it.

## Structural units — feature, sub-feature, component, action, cross-feature

Every grantable row on the Permissions page declares one of these kinds
(`RowKind` in
`interfaces/dashboard/src/features/permissions/permRows.ts` — the row model
is the enforced mirror of this section; `permMatrix.test.ts` and
`verbGrid.test.ts` pin it):

| Unit | Rule (checkable, not vibes) | Examples |
|---|---|---|
| **Feature** | own surface + lifecycle (its row = the front door; untagged row grants VIEW) | Vehicles, Live Map, Parts |
| **Sub-feature** | own HOME — a folder with its own hub contributions (`report.py` / `ai_tool.py` / `alert.py` / `scoring_signal.py`) — nested under a parent family; rides the parent's router/service | Health, Faults, Fuel, Efficiency under `features/vehicles/<x>/` |
| **Component** | flag-gated part of the parent's surface, NO home of its own | POI Layers (of Live Map), Driver roster (of Drivers), Fuel Costs · Cost per Mile (of Costs) |
| **Feature action** | a do/write verb on one feature.  A generic one renders as the parent's **Manage column**, not a row; only a SPECIFIC verb no column can express stays a row | Manage (Vehicles · Loads · Carrier Directory) → columns; **Hire Applicant** → the one action row |
| **Cross-feature** | a do-verb that spans features, owned by none.  Never nested, never per-feature.  NOT called "capability" — that word is the four hubs' | the config family (`can_manage_config_role` / `_all`, capabilities/config/docs/ARCHITECTURE.md) |

**Row = noun, column = verb.**  The Permissions page puts features down and
verbs across (View · Manage · Config), so a row that names its own verb
("Manage POI Layers") says it twice.  The row is the object; the column
supplies the action.

**Graduation path** (each step is a real structural change, not a rename):

```
component  →  sub-feature  →  feature
(flag only)    (gains own home     (gains own surface,
                + contributions)     lifecycle, nav entry)
```

A sub-feature MAY grow its own action/component rows (they nest under it,
depth 2 via `parentKey`); a component may NOT — needing an action or config
participation is the graduation signal.  Cross-feature rows never nest and
never multiply per feature.

**Derived service flags** — `can_alerts_all/_vehicle`, `can_ai_chat`,
`can_digest` — exist in `FeatureSet` but are COMPUTED
(`derive_service_perms`), never granted: live enforcement for the
always-on services, deliberately without rows of their own. The drift-guard
test (`tests/test_permission_surface.py`) holds every `FeatureSet` field
to exactly one home: a permissions row, the Driver tab, the derived list,
or the explicit exempt list.

## The feature → component tree

> **Alerts · AI · Reports** are **system services**, not features — they have
> no matrix rows and live in [`SERVICES.md`](SERVICES.md). The report *types*
> they surface (Risk Summary, Cost Reports) are features and now sit under
> their owning departments (Safety, Accounting), below.

### 🟨 Personal
| Feature | Parts |
|---|---|
| **Profile** | personal prefs (name/language/timezone) · DND toggle.  Lives in `src/pages/` — placement isn't taxonomy.  "My Notifications" is Alerts', not Profile's |
| **Own records** | the view-own twins of department features: Own Documents · Own Paystubs · Own Coaching · Own Loads · Own Risk Summary (edited on the Permissions page's **Driver** tab) |

### 🟦 Administration
| Feature | Home |
|---|---|
| **Permissions / Integrations / Storage** | own pages, Account nav group; backend `capabilities/{permissions,integrations,storage}/` |
| **General settings** | the Settings page — timezone, bot + forum routing, department modules; `features/settings/account/` |
| **Team Management** | members, roles, data scope; also gates the Audit Log; `features/settings/team_management/` |
| **Send Invites** | `features/settings/invites/` |
| **Manage Companies** | `features/settings/companies/` |
| **Working Hours** | schedules feed the DND gate in `capabilities/alerting/`; `features/settings/work_hours/` |
| *(components, no flag of their own)* | **Audit Log** rides `can_manage_users` (`features/settings/audit/`) · **forum routing** and bot config ride `can_manage_account` |

**Tier and nav group are independent on purpose.** These eight sit in three
different sidebar groups — `settings` (Team Management, Companies, Settings,
Audit Log), `account` (Permissions, Integrations, Storage), `people` (Send
Invites). That is not drift: `tier` answers *who is this for*, `navGroup`
answers *where does a user look for it*, and Invites belongs next to People
even though it governs the account. Only `modules` was aligned (2026-07-30):
Invites had `['hr','account']`, the one Administration entry that read as
department-owned — inert in practice, since `account` is not a toggleable
department, so it was always surfaced anyway.

> These eight were called "Settings components" until 2026-07-30.  The
> catalog already gave each its own entry, path and permission, and the
> backend already gave each its own folder + router — only this doc and the
> permissions rows still said component.  The Settings page groups several
> of them in the NAV; grouping was never ownership.

### 🟩 Shared (persona-composed pages)
| Feature | Components |
|---|---|
| **Overview** | greeting · alert strip · AI fleet brief · status grid · KPI grid — the purest persona-composed page; no permission of its own (each section is gated by what the role can already see) |
| **Knowledge Base** | articles · categories · approval workflow · bookmarks · uploads — every department reads the same thing; no permission |
| **Vehicles** | list · detail sections: health, faults, location, timeline, usage, inspections |
| **Drivers** | profiles · documents (+ Own Documents → Personal) · **Driver roster** (component: invite, assign trucks, TMS links) · expiry |
| **Live Map** | map · overlays · **POI Layers** (component — the Live Map grant shows them, its own flag edits them) |
| **Geofences** | zones CRUD · entry/exit alert contribution |
| **Scorecards** | scoreboard (viewer) · **Scorecard Rules** (config component — gated by the config family's `can_manage_config_all`, capabilities/config/docs/ARCHITECTURE.md) · scoring engine + signals (backend) · drop-alert contribution |

### 🟪 Role
| Department | Features (components) |
|---|---|
| Fleet | Maintenance (tasks · calendar · custom types) · Work Orders (+ invoice upload · cost-report contribution) · **Vendors** (registry · profile w/ spend history · merge — referenced by Work Orders, never owned by it; global directory is a platform sub-family sibling, see capabilities/platform/docs/vendor-parts-master-data.md) · **Parts** (catalog · per-part analytics: recurrence per vehicle, price per vendor · merge — graduated from a Work Orders component 2026-07-16, feature-owned `can_parts`; WO consumes it via line resolve + autocomplete) · PTI Inspections (+ template) · **Truck Anatomy** (3D learning model of the rig, taxonomy-as-scene-graph — DARK FEATURE: `can_truck_anatomy` seeded to NOBODY incl. owner; self-granted on the Permissions page when marketed) |
| Dispatch | Routes |
| Safety | Safety Events · Cameras · **Parking** (unsafe-parking events only) · **Risk Summary** (the stakeholder/personnel risk report — a Reports-hub *tab*, owned here; `can_risk_report_*`) |
| HR | Coaching (+ View Own) |
| Accounting | **Costs** (Fuel Costs + Cost per Mile components) · **Cost Reports** (executive cost rollups — a Reports-hub *tab*, owned here; `can_cost_reports`) · Driver Pay (+ View Own Paystubs) · Billing |

## Money domains — who is the family, who is the child (decided 2026-07-10)

**Billing is the platform-money FAMILY** (the industry-standard parent —
Stripe's own model): the whole machinery of us charging the customer account.
Its children are the charging *shapes* — subscription (the recurring
agreement), invoices (the claim), comp windows, enforcement, and future
payments / payment methods / one-time purchases.  Rooting the family at
"subscription" was considered and rejected: the first non-subscription money
object (a one-time purchase) would make a dedicated purchases module under `capabilities/platform/billing/`
a lie, and every frozen wire contract already says billing.

Because code family = wire word, **nothing is misnamed**: `/billing/*` URLs,
`can_manage_billing`, `BILLING_*` env vars, `billing_*` tables and job ids
are all simply correct.  The customer-facing LABEL is **"Billing"** too (nav
"Billing", page "Billing & Plan"), so the label and the family word match.
(History: a "Subscription" label was tried 2026-07-10 and **reverted
2026-07-13** — it read as its own feature and blurred the line with Driver
Pay.  "subscription" stays only as a Stripe *domain* term — the recurring
agreement, the `billing_subscriptions` object — never as the page label.)

**The one rule — "Whose money is it?" — decides where anything money-shaped
goes:**

| Whose money | Family (root) | Home | Children today → future |
|---|---|---|---|
| OURS, from the customer | **Billing** (label: "Billing") | `capabilities/platform/billing/` | subscription plan/tier, invoices, comp, enforcement → payments, payment methods, one-time purchases |
| The CUSTOMER’s, to their drivers | **Driver Pay** (label: "Driver Pay"; physical tables kept `payroll_runs`/`payroll_run_items`, dormant col `accounts.payroll_enabled` — internal legacy, see note below) | `features/driver_pay/` | runs, statements, bonus rules, pay models |
| The CUSTOMER's, from their brokers | **Invoicing** (receivables) | future `features/invoicing/` | broker invoices, factoring, settlements-in |

Three trees, three owners, never a shared parent.  "billing" unqualified
always means the platform family — the customer→broker feature is named
**invoicing**, never billing.  Adding platform "payments" = new module INSIDE
`capabilities/platform/billing/`, never a sibling folder.

> **Driver Pay's "payroll" internal legacy — NOT billing.** The feature was
> named "Payroll" until 2026-07-13, then renamed **Driver Pay** everywhere a
> human or a new contract sees it (feature dir `features/driver_pay/`, router
> `/driver-pay`, flags `can_driver_pay_*`, mixin `DriverPayMixin`, UI, bot).
> Three deepest storage identifiers were **deliberately kept** under the old
> word — tables `payroll_runs` / `payroll_run_items` (+ their indexes) and the
> dormant column `accounts.payroll_enabled` — because renaming physical tables
> hit a dual-DB + `CREATE TABLE IF NOT EXISTS` reboot-orphan problem for zero
> user-visible gain (the exact call Billing made for its `billing_*` tables).
> These `payroll_*` tables belong to **Driver Pay and only Driver Pay** — no
> billing code touches them and no driver-pay code touches `billing_*`. They
> live beside every other table in `migrations.py` / `platform_migrations.py`
> only because that's the one shared schema file. "payroll" also survives as a
> distinct **report-audience** enum (`audience=payroll`, "Safety-Pay Evidence")
> in `capabilities/reporting/` — a pay-by-score evidence report, unrelated to
> the Driver Pay feature.

### The platform sub-family — audience split inside capabilities/

`capabilities/` holds two audiences, made structural on 2026-07-10:

- `capabilities/<x>/` (alerting, reporting, ai, scorecards, warehouse, …) —
  **tenant-serving machinery**: exists to power the customer's features.
- `capabilities/platform/<x>/` — **system-owner domains**: serve 4truck the
  operator, not the customer's daily work.  Members: `billing` (today);
  account-purge + operator-console services (future candidates).

Boundary rules, CI-enforced by `tests/test_layer_boundaries.py`:
`features/**` never imports `capabilities.platform.*`, and
`capabilities/platform/**` never imports `features.*`.  (Deliberate seam
that remains legal: tenant machinery MAY call INTO platform — e.g. the
Samsara ingest cycle triggers `billing.sync_billing_quantity` — because
capabilities→platform is a one-way service call, not a product dependency.)
This is NOT `infra/`: infra is bottom-layer technical plumbing (cache,
crypto, config) imported by everything; platform domains are top-layer
business domains with routers/jobs/notifications.

## Backend patterns (implementation, orthogonal to tier)

- **Hubs** (the four true *capabilities*: Alerting, Reporting, AI, Scorecards):
  registry + shared core (pipeline/escalation/dnd/routing; data_fetch/
  pdf_base; engine/rules). Each source feature contributes a module the hub
  collects. **"Hub" is an implementation pattern, not an access model**: three
  hubs (Alerting, Reporting, AI) are reached as always-on **services** (see
  [`SERVICES.md`](SERVICES.md)); **Scorecards is a hub but stays a gated
  *feature*** (`can_scorecard_all/vehicle` is a matrix toggle — driver-
  behaviour data is role-sensitive), which is why its viewer is Shared-tier.
- **Features** (entities + workflows + standalone, tier-agnostic): the domain
  data contract (`service.py`) + the feature's per-hub contributions.
  Telemetry aspects are **components of Vehicle** (health, fuel, faults,
  efficiency, odometer) and of **Driver** (safety_events, hos,
  documents) — each component folder owns its
  `alert.py / report.py / ai_tool.py / scoring_signal.py`.
- **Frontend is feature-centric** (mirrors the backend): every product
  feature has ONE home `interfaces/dashboard/src/features/<feature>/`
  holding its page(s) + feature-specific components + (if persona-composed)
  `registry.ts` + `layouts.ts` + `sections/` rendered by
  `features/_lib/PageLayoutHost`. `src/pages/` holds ONLY app-shell pages
  (Login/auth flow, NotFound, Profile); `src/components/` holds ONLY shared
  primitives (`ui/`, `shell/`). Settings components live in
  `features/settings/`; Billing in `features/billing/`. System hub pages
  use the SAME PageLayoutHost engine — System vs Shared is a *backend*
  distinction (aggregate-from-many vs serve-one-entity), not a frontend one.
- **Shared backend rule**: per-section API endpoints (`routes/<feature>.py`),
  gated by permission + data scope only — **no persona logic in the backend**;
  un-rendered sections never fire their queries.

## Target directory layout (agreed 2026-06-10; ships WITH the migration)

Folders encode the **dependency direction** (hubs → features → platform),
never the tier. The tier lives in exactly one place — `featureCatalog.ts` —
so re-tiering a feature (as Parking Shared→Role was) stays a one-word edit,
never a file move. A System-tier feature like Knowledge Base still lives in
`features/` — tier and folder are orthogonal on purpose.

```
features/                       ← ALL product features, tier-agnostic
  vehicles/    service.py + health/ fuel/ faults/ efficiency/ …
  drivers/     service.py + safety_events/ hos/ documents/ …
  geofencing/  parking/  routes/
  maintenance/ work_orders/ inspections/
  coaching/    driver_pay/  costs/
  knowledge/

capabilities/                   ← ONLY the four hubs (true capabilities)
  alerting/    pipeline · escalation · dnd · routing · forum · registry
  reporting/   registry · data_fetch · pdf_base · csv · audiences
  ai/          intelligence · chat · vision · models · tools-registry
  scorecards/  engine · rules · curves · signals registry

(platform substrate — permissions (role model + module gating + router),
 telemetry ingestion/warehouse, formatting,
 notifications, localization, media — is plumbing every feature uses, not a
 feature; it stays in capabilities/ or splits to platform/ at migration time)
```

Each feature component registers into a hub
(`features/vehicles/health/alert.py` → Alerts registry) exactly like a
workflow does (`features/cameras/alert.py`) — same shape, same folder
family, regardless of tier. Frontend `src/features/` ↔ backend `features/`
become a 1:1 mental model.

### Routers live with their features — DONE 2026-06-10

The vertical-slice completion: 14 feature-owned routers moved to
`features/<x>/router.py` (vehicles, drivers, parking, coaching, driver_pay,
costs, knowledge, maintenance, work_orders, pti, routes, geofencing,
location/router + location/pois), and `safety.py` split three ways:
scorecard endpoints now live at `features/scorecards/router.py` (the
Scoring hub's surface), events → `features/events/router.py`, cameras →
`features/cameras/router.py` — all keeping the historical
`/safety` URL prefix. Verified by **route-table parity: 782 routes,
byte-identical** before/after.

Standing rules (tightened by the consistency pass, same day):
- **Every domain router lives with its domain** — `features/<x>/router.py`
  or `capabilities/<hub|platform-cap>/router.py` (alerting, reporting, ai,
  scoring, billing, integrations, storage all co-located).
- `interfaces/api/` is ONLY the assembly shell: app/middleware, `deps.py`,
  and the genuinely cross-feature/governance/personal routers (`auth`,
  `user`, `admin`, `permissions`, `webhooks`, `system`, `health`).
  `fleet.py` turned out to be a URL-prefix-named mixed bag and was
  decomposed by owner: `/fleet/overview/stats` → **`features/overview/router.py`**
  (Overview finally has a backend home, 1:1 with its frontend folder;
  it's a pure aggregator — no service of its own), `/fleet/overview` +
  `/fleet/weather` → vehicles `fleet_router` (vehicle list + cabin
  sensor readings), `/fleet/utilisation/heatmap` → parking
  `fleet_router` (it reads `parking_events`). URLs unchanged.
- Buried component endpoints extracted to their owners: scorecard-rules +
  pillar-caps + storage-quota out of `admin.py`; `/user/me/alerts`
  (My Notifications → alerting `user_router`) and
  `/user/scheduled-reports` (→ reporting `user_router`) out of `user.py` —
  all URLs unchanged (extra routers keep the historical prefixes).
  SUPERSEDED 2026-07-30: Companies / Invites / **Working Hours** /
  account settings / audit no longer live in `admin.py` — each has its own
  folder and router under `features/settings/<x>/`, which is why they are
  **Administration features**, not Settings components (the earlier
  "Working Hours reverted to a component" note described a code layout that
  no longer exists).  Split responsibilities that DO stay split: the DND
  gate lives in `capabilities/alerting/` (on_shift.py / dnd.py) while the
  work-hours schedule CRUD is a thin tenant_db pass-through in
  `features/settings/work_hours/`.
- **Dependency exception, stated once**: `router.py` is interface-layer
  code co-located with its feature — ONLY `router.py` may import
  `interfaces.api.deps`; `service.py`/`alert.py`/etc. never may. (The
  note is stamped at the top of every moved router.)

### Service contracts — ENFORCED 2026-06-10

Tools/hubs read domain data ONLY through services. The 21
`account_id`-absent fallback branches that called `samsara_client`
directly were removed (all production paths guarantee account context);
three new accessors created: `telemetry.service.get_engine_states`,
`features/drivers/service.get_drivers`,
`media.service.get_dashcam_snapshots`. Acceptance: zero
`samsara_client.` calls in any tool file.

> **Current state (2026-06-10)**: the `features/` split is LIVE — the 14
> feature packages moved out of `capabilities/` (imports renamed repo-wide),
> and all three hub registries exist: alert sources self-register via
> `@register_alert_source` (`capabilities/alerting/registry.py`; the
> scheduler loops the registry), scoring signals via `SIGNALS`
> (`capabilities/scorecards/signals/__init__.py`), AI tools + reports were
> already registry-driven. The per-source hub carve has since SHIPPED:
> the health source now lives at `features/vehicles/health/alert.py`
> (this note once tracked it as open — the doc lagged the code).

## Known follow-ups
- ~~Convert the plain shared pages (Drivers, Scorecards)~~ **DONE 2026-06-10**:
  all five Shared pages are persona-composed. Drivers uses per-persona drawer
  TABS (`interfaces/dashboard/src/features/drivers/personaConfig.ts`), Scorecards per-persona page
  BLOCKS (`interfaces/dashboard/src/features/scorecards/personaConfig.ts`) — the tab/block
  flavor for list pages, vs the PageLayoutHost section flavor for detail
  pages. Both locked by personaConfig tests. Parking/Costs are Role-tier
  plain pages and stay plain.
- ~~Component carving~~ **DONE 2026-06-10** — all seven components carved:
  `features/vehicles/{health,fuel,faults,efficiency}/` (+ `features/cameras/` — a Safety FEATURE, not a Vehicle component),
  `features/events/` (renamed from safety_events — no "safety" prefix in
  feature code/folders; the telemetry/warehouse layer keeps its
  `safety_event_log` table + ingest naming, that's platform), and
  `features/drivers/documents/` (the doc-expiry alert source, the first
  carve out of `interfaces/bot/`). Each component owns its hub
  contributions (`alert.py / report.py / ai_tool.py / scoring_signal.py`; efficiency
  also has `vehicle_scoring_signal.py`). The hubs now hold ONLY shared machinery —
  alerting: pipeline/escalation/dnd/routing/forum/registry; reporting:
  registry/data_fetch/pdf_base/csv/audiences + composites (vehicle, shift,
  risk, DOT); ai: chat/models/tools-registry + cross-feature tools;
  scoring: engine/rules/SIGNALS.
  **The carve recipe** (for future components): git-mv the hub files into
  `features/<feature>/<component>/`, convert relative imports to absolute,
  update the importers (hub `__init__`s, registries, scheduler), and obey
  the two cycle rules: (1) component `__init__.py` is docstring-only —
  re-export NOTHING; (2) hub `__init__`s never re-export feature modules —
  consumers import the component directly. Verify with the 4-registry
  parity check (scheduler job ids, AI tool registry, SIGNALS, ReportSpec
  generator modules).
- Service-contract enforcement (NOT yet done): hubs/components read only
  domain `service.py`, never Samsara/warehouse directly — AI tools are the
  worst offender today. This is the prerequisite for a real
  `features/vehicles/service.py` data contract.
- Optionally move Scorecard Rules UI into a tab of the Scorecards page.


## URL history — the /fleet/* prefix retirement (2026-06-11)

The legacy `/fleet/*` prefix (a persona-subdomain-era artifact that
matched no feature) and the hidden backwards-compat alias routers were
removed.  Each endpoint now lives under its own feature prefix; the old
URLs are recorded here as history (no live redirects — frontend, miniapp
and tests all moved in lockstep):

| Old URL | New URL |
|---|---|
| `/fleet/overview/stats` (+ alias `/dashboard/stats`) | `/overview/stats` |
| `/fleet/overview` | `/vehicles/overview` |
| `/fleet/weather` | `/vehicles/weather` |
| `/fleet/utilisation/heatmap` | `/parking/utilisation/heatmap` |
| `/fleet/geofences` (+ alias `/map/geofences`) | `/geofences` |
| `/fleet/routes` (+ alias `/dispatch/*`) | `/routes` |

Bonus fix: `/vehicles/utilization-summary` was previously **shadowed** by
the `/{vehicle_name}` catch-all (it resolved to `vehicle_detail`); the
static vehicle routes are now declared before the catch-all, so the
Utilization Summary page reaches its real handler.  Rule going forward:
**no borrowed prefixes, no hidden alias routers** — one feature, one
prefix; static routes before any `/{param}` catch-all.
