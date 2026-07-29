# Feature taxonomy — the 3-tier model

Decided 2026-06-10. This is the SSOT for how we classify product surface.
The catalog (`interfaces/dashboard/src/config/featureCatalog.ts`) encodes the
tier per feature; the Permissions matrix groups by tier → department.

> **Alerts, the AI assistant, and Reports are NOT features** — they are
> always-on **system services** (every role has them; access is *derived*, not
> toggled). They have a different architecture and their own SSOT:
> [`SERVICES.md`](SERVICES.md). This doc covers only the toggleable feature
> taxonomy; it points to SERVICES.md for the service layer and never duplicates
> it.

## The axes (each answers ONE question)

| Axis | Question | Mechanism |
|---|---|---|
| **Tier** (System / Shared / Role) | how broadly does the feature apply? | catalog `tier` |
| **Module** | is the department switched on for the account? | the Permissions matrix band-switches (`capabilities/permissions/modules.py`) |
| **Permission** | may this role open it? | Permissions matrix (`can_*` flags) |
| **Data scope** | whose data do they see? (All / Company / Vehicle) | Team Management, per-user |
| **Backend pattern** (hub / entity / workflow) | how is it implemented? | not a tier — see below |

"Admin" is **not** a tier or feature category — it's the permission axis
(`can_manage_*` gates on System-tier governance features).

A **feature** has its own identity and lifecycle. A **component** is a part of
one (a config panel, a preferences page, a viewer, a sub-permission). Tiers
classify features only; components inherit their parent's tier.

## Tier definitions

1. **System** — works account-wide regardless of department modules. The
   System-tier *features* are *core standalone* surfaces: Knowledge Base, the
   governance/config pages (Permissions, Integrations, Storage, Settings),
   Overview (an aggregator page), and personal pages (Profile). The
   *aggregator hubs* Alerts / AI / Reports are **no longer features** — they
   became always-on **system services** (see [`SERVICES.md`](SERVICES.md)); an
   owner shapes them only through the features that feed them. Hub *machinery*
   carries no tier — only user-facing surfaces do; e.g. the Scoring hub is
   backend-only and its viewer page (Scorecards) is Shared.
2. **Shared** — one entity/view useful to several departments, but the page
   **composes different components per role** (persona layouts). Visible to
   all permitted roles; relevance differs.
3. **Role** — a single department's workflow; surfaced only when that
   department's module is on. A Role feature MAY list several modules
   (e.g. Safety Events: safety + hr) — tier describes its single-workflow
   nature, modules describe who surfaces it.

## Structural units — feature, sub-feature, component, action, capability

Every grantable row in the Permissions matrix declares one of these
kinds (`RowKind` in `interfaces/dashboard/src/features/permissions/Permissions.tsx` — the matrix
is the enforced mirror of this section):

| Unit | Rule (checkable, not vibes) | Examples |
|---|---|---|
| **Feature** | own surface + lifecycle (its row = the front door; untagged row grants VIEW) | Vehicles, Live Map, Parts |
| **Sub-feature** | own HOME — a folder with its own hub contributions (`report.py` / `ai_tool.py` / `alert.py` / `scoring_signal.py`) — nested under a parent family; rides the parent's router/service | Health, Faults, Fuel, Efficiency under `features/vehicles/<x>/` |
| **Component** | flag-gated part of the parent's surface, NO home of its own | Team Management, Working Hours (of Settings) |
| **Feature action** | a do/write verb on one feature (the "Manage" rows; a specific verb when it isn't generic admin — "Hire Applicant") | Manage under Vehicles, Manage POI Layers |
| **Cross-feature capability** | spans features; never nested, never per-feature | the config family (`can_manage_config_role` / `_all`, docs/architecture/config.md) |

**Graduation path** (each step is a real structural change, not a rename):

```
component  →  sub-feature  →  feature
(flag only)    (gains own home     (gains own surface,
                + contributions)     lifecycle, nav entry)
```

A sub-feature MAY grow its own action/component rows (they nest under
it in the matrix, depth 2 via `parentKey`); a component may NOT — needing
an action or config participation is the graduation signal. Capabilities
never nest and never multiply per feature.

**Derived service flags** — `can_alerts_all/_vehicle`, `can_ai_chat`,
`can_digest` — exist in `FeatureSet` but are COMPUTED
(`derive_service_perms`), never granted: live enforcement for the
always-on services, deliberately without matrix rows. The drift-guard
test (`tests/test_permission_surface.py`) holds every `FeatureSet` field
to exactly one home: matrix row, driver panel, derived list, or the
explicit exempt list.

## The feature → component tree

> **Alerts · AI · Reports** are **system services**, not features — they have
> no matrix rows and live in [`SERVICES.md`](SERVICES.md). The report *types*
> they surface (Risk Summary, Cost Reports) are features and now sit under
> their owning departments (Safety, Accounting), below.

### 🟦 System
| Feature | Components |
|---|---|
| **Overview** | greeting · alert strip · AI fleet brief · status grid · KPI grid (a System-tier *feature* — an aggregator page, permission-gated by what it shows) |
| **Knowledge Base** | articles · categories · approval workflow · bookmarks · uploads |
| **Settings** — ONE governance feature; components are FLAT siblings, each with its own permission so administration can be one role's or delegated piecemeal (STEP 2 DONE: granular flags live, migration 102 defaults them from stored can_manage_account. NAV: the components collapse under ONE "Settings" sidebar parent — closed by default, auto-open while a child route is active, flag-filtered per user. MODULES: the standalone page is GONE — department on/off switches live on the Permissions matrix section headers, riding can_manage_account; /admin/modules redirects to /admin/permissions) | *components (flag):* **Account Settings** (`can_manage_account`: timezone · bot config · forum routing · AI usage · Modules) · **Team Management** (`can_manage_users`; also Audit Log; its page HOSTS sibling components as tabs — UI hosting ≠ taxonomy) · **Invites** (`can_invite`) · **Working Hours** (`can_manage_work_hours`; schedules feed the DND gate in `capabilities/alerting/`) · **Companies** (`can_manage_companies`) · (Permissions, Storage, Integrations are NOT Settings components — they're standalone **System features** backed by capabilities/, in the Account nav group) |
| **Permissions / Integrations / Storage** | standalone System-tier features (own pages, Account nav group); backend = `capabilities/{permissions,integrations,storage}/` (shared infra, like Alerts↔alerting). NOT Settings components. |
| **Profile** | personal prefs (name/language/timezone) · DND toggle · My Notifications is Alerts', not Profile's |

### 🟩 Shared (persona-composed pages)
| Feature | Components |
|---|---|
| **Vehicles** | list · detail sections: health, faults, location, timeline, usage, inspections |
| **Drivers** | profiles · documents (+ View Own) · assignments · expiry |
| **Live Map** | map · overlays · Manage POI Layers |
| **Geofences** | zones CRUD · entry/exit alert contribution |
| **Scorecards** | scoreboard (viewer) · **Scorecard Rules** (config component — gated by the config family's `can_manage_config_all`, docs/architecture/config.md) · scoring engine + signals (backend) · drop-alert contribution |

### 🟪 Role
| Department | Features (components) |
|---|---|
| Fleet | Maintenance (tasks · calendar · custom types) · Work Orders (+ invoice upload · cost-report contribution) · **Vendors** (registry · profile w/ spend history · merge — referenced by Work Orders, never owned by it; global directory is a platform sub-family sibling, see docs/architecture/vendor-parts-master-data.md) · **Parts** (catalog · per-part analytics: recurrence per vehicle, price per vendor · merge — graduated from a Work Orders component 2026-07-16, feature-owned `can_parts`; WO consumes it via line resolve + autocomplete) · PTI Inspections (+ template) |
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
object (a one-time purchase) would make `subscription/one_time_purchases.py`
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
workflow does (`features/maintenance/alert.py`) — same shape, same folder
family, regardless of tier. Frontend `src/features/` ↔ backend `features/`
become a 1:1 mental model.

### Routers live with their features — DONE 2026-06-10

The vertical-slice completion: 14 feature-owned routers moved to
`features/<x>/router.py` (vehicles, drivers, parking, coaching, driver_pay,
costs, knowledge, maintenance, work_orders, pti, routes, geofencing,
location/router + location/pois), and `safety.py` split three ways:
scorecard endpoints stayed as `interfaces/api/routes/scorecards.py` (the
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
  Deliberate stays: Companies/Invites/**Working Hours**/bot-config/
  forum-routing remain in `admin.py` — they are Settings/Team-Management
  COMPONENTS and admin.py IS that governance router. (Working Hours was
  briefly given a standalone router; reverted — it's a Team Management
  component per the tree above, and the component rule wins. Its backend
  the DND gate lives in `capabilities/alerting/` (on_shift.py/dnd.py); the work-hours schedule CRUD is a thin tenant_db pass-through in features/settings/work_hours/.)
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
> already registry-driven. NOT yet done: carving the per-source hub modules
> into feature component folders (`capabilities/alerting/health.py` →
> `features/vehicles/health/alert.py`) — the hubs still own those files.

## Known follow-ups
- ~~Convert the plain shared pages (Drivers, Scorecards)~~ **DONE 2026-06-10**:
  all five Shared pages are persona-composed. Drivers uses per-persona drawer
  TABS (`src/features/drivers/personaConfig.ts`), Scorecards per-persona page
  BLOCKS (`src/features/scorecards/personaConfig.ts`) — the tab/block
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
