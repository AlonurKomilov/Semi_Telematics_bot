# Persona / role architecture

This codebase serves **one customer-facing dashboard SPA** at **N
per-role subdomains** of `4truck.us`.  Each subdomain is a per-role
workspace; the SAME built bundle is served at every subdomain and the
active role is derived from `window.location.host` at boot.  This is
"deployment-level separation feel" with "codebase-level cohesion" —
operators see a dedicated workspace per role; engineers maintain
one codebase.

## Subdomains in production

| Host | Persona | Default landing |
|---|---|---|
| `dash.4truck.us` | Owner / Admin (executive) | `/` (Overview) |
| `fleet.4truck.us` | Fleet manager | `/live-map` |
| `dispatch.4truck.us` | Dispatcher | `/live-map` |
| `safety.4truck.us` | Safety manager | `/scorecards` |
| `hr.4truck.us` | HR | `/workforce/drivers` |
| `accounting.4truck.us` | Accounting | `/cost-reports` |
| `app.4truck.us` | Mini App (drivers) | — |
| `system.4truck.us` | Platform operator console (NOT for customers) | — |
| `api.4truck.us` | FastAPI backend | — |
| `bot.4truck.us` | Telegram webhook | — |

DNS, TLS (`*.4truck.us` wildcard), and nginx routing are documented in
[runbooks/subdomain-rollout.md](../runbooks/subdomain-rollout.md).

## The two layers of "what each role sees"

### 1. Page-level (sidebar nav)

Per-role shell files in `interfaces/dashboard/src/shells/` choose
which pages appear in the sidebar.  A Safety user's sidebar never
shows "Maintenance" because `SafetyShell`'s nav doesn't include it —
the route still exists but isn't discoverable from Safety's chrome.

Add a new persona-specific page: add the entry to the matching shell's
nav config and a route in `router.tsx`.

### 2. Component-level (sections inside shared pages)

Shared pages (Overview, Alerts, Live Map, Vehicle detail) compose
**different sections per persona** via Pattern B.  Each feature has:

```
interfaces/dashboard/src/features/<feature>/
  layouts.ts           ← per-persona section list
  registry.ts          ← section id → lazy component
  personaConfig.ts     ← per-persona presentation knobs (defaults,
                          priorities, header copy)
  sections/            ← the section components themselves
  <Feature>.tsx        ← thin page wrapper using PageLayoutHost
```

`layouts.ts` is the source of truth for "which sections render on
persona X's version of this page."  Sections themselves are
**persona-agnostic** — they receive everything they need via
`sectionProps` and never read `useShellConfig` directly (enforced by
`scripts/check-role-drift.mjs`).

`personaConfig.ts` carries presentational config like header copy and
KPI priorities — same pattern: page wrapper resolves once, sections
receive resolved values.

## How the active persona is decided

In [`RoleViewContext.tsx`](../../interfaces/dashboard/src/context/RoleViewContext.tsx)
the `activeView` is **derived** (not stored as state) from this
resolution order:

1. User's underlying JWT role.  For non-switchable roles
   (`fleet`/`safety`/`dispatcher`/`hr`/`accounting`/`driver`) the
   active view IS their role — they cannot view as another persona.
2. For switchable roles (Owner/Admin):
   1. Explicit user choice (localStorage `roleView.activeView` from
      the persona-selector dropdown).
   2. Branded subdomain hint (`fleet.4truck.us` → Fleet view).
   3. Their real role (Owner-on-`dash.4truck.us` → Owner view).

The persona selector pill in the topbar lets Owner/Admin switch.
Switching to a persona whose home is a different subdomain navigates
via `window.location` so the URL bar reflects the active workspace
(bookmarkable, shareable).

## Strict role binding — backend contract

Persona-aware count endpoints respect the user's **active view**, not
just their JWT role.  Owner-as-Owner sees owner-scoped counts;
Owner-as-Fleet sees Fleet's counts (same JWT, different scope).

Mechanism:

1. **Frontend** ([api/client.ts](../../interfaces/dashboard/src/api/client.ts))
   sends `X-View-As: <activeView>` on every authenticated request.
   `setActiveViewForApi(view)` is called from `RoleViewContext`
   whenever the derived `activeView` changes.

2. **Backend dep** [`active_view` in deps.py](../../interfaces/api/deps.py)
   reads the header, validates against the user's role (owner/admin
   can preview anything; others can only view-as themselves), and
   returns the effective role for filtering.  Never raises — falls
   back to the JWT role on any invalid / missing input.

3. **Persona mapping**
   [`capabilities/alerting/persona_mapping.py`](../../capabilities/alerting/persona_mapping.py)
   resolves a role to its persona's alert types via
   `alert_types_for_role(role)`.  Owner / Admin's persona is
   `owner_admin` whose types are just `{system, reescalate}` (the
   cross-cutting digest signals — NOT operational alerts).

4. **Endpoints** that need persona scoping take `Depends(active_view)`
   and filter using the returned role's alert_types.  Today these
   are:
   - `GET /api/fleet/overview/stats` — `pending_alerts`
   - `GET /api/alerts/pending/count` — tab badge

## Strict role binding — frontend contract

The frontend mirrors the backend:

- `interfaces/dashboard/src/features/overview/personaConfig.ts`
  `ROLE_KPI_PRIORITY[persona]` lists ONLY the KPI cards that belong
  to that persona's workspace.  Cross-persona cards (e.g. Owner
  seeing the `faults` card) are removed from the priority array.
  The `OverviewKpiGrid` filters its all-KPIs list against this.
- `OverviewAlertStrip` gates each row by `kpiPriority.has(key) &&
  has('can_X')` — both the view-relevance check AND the
  permission check must pass.
- Per-persona hero strips in `shells/heroes/*Hero.tsx` only render
  chips relevant to that persona.  Cross-persona chips were removed
  during the strict-binding pass (e.g. SafetyHero dropped
  `unsafe_parking` + `faults`; FleetHero dropped `low_fuel`).
- `Vehicles.tsx` columns are persona-filtered via
  `PERSONA_EXTRA_COLUMNS` (universal cols always shown; persona-
  specific extras come from the active view).
- `Reports.tsx` tabs filtered via `TABS_FOR_VIEW`; Safety lands on
  Efficiency, Fleet sees all four, Accounting sees Fuel + Efficiency.
- Live Map overlays composed per-persona via
  `interfaces/dashboard/src/features/live-map/layouts.ts`.  Each overlay is a Pattern B
  section that imperatively mounts a Leaflet layer.  Persona
  ownership:
  - **Owner / Admin**: `company_color_partition` + `utilisation_heatmap`
  - **Fleet**: `fault_markers` + `maintenance_markers`
  - **Dispatcher**: `geofence_boundaries` + `unsafe_parking_markers`
  - **Safety**: `safety_heatmap` (auto-on)
  - **Accounting**: `company_color_partition`
  - **HR / Driver**: base vehicle layer only
  Strict-binding tests in `interfaces/dashboard/src/features/live-map/registry.test.ts` lock this in.

## Backend endpoints that respect persona (today)

| Endpoint | Persona filter mechanism |
|---|---|
| `GET /api/fleet/overview/stats` (`pending_alerts`) | `Depends(active_view)` → `alert_types_for_role` |
| `GET /api/alerts/pending/count` | Same |
| `GET /api/safety/events/heatmap?days=N` | Permission gate `can_events_*` (Safety + Owner/Admin) |
| `GET /api/admin/escalations` | Permission gate `can_alerts_all` (Owner / Admin) |
| `GET /api/alerts/aggregate?days=N` | Same |
| `GET /api/safety/events/summary?days=N` | Permission gate `can_events_*` |
| `GET /api/coaching/assignments/count` | Permission gate `can_manage_coaching` / `can_view_coaching` at person width `all` |
| `GET /api/maintenance/due-locations` | `has_maintenance_access` + assigned-width truck scoping |
| `GET /api/fleet/utilisation/heatmap?days=N` | `require_wide("vehicles")` — `can_view_vehicles` at unit width `all` |
| `GET /fleet/geofences` | Permission gate `can_geofence_*` |
| `GET /parking/active` | Permission gate `can_alerts_*` / `can_vehicle_all` |

## Adding a new persona — checklist

1. Add the value to `Persona` union in
   [features/_lib/types.ts](../../interfaces/dashboard/src/features/_lib/types.ts).
2. Decide if it gets its own subdomain.  If yes:
   - Add to `SUBDOMAIN_TO_ROLE` and `ROLE_HOST` in
     [RoleViewContext.tsx](../../interfaces/dashboard/src/context/RoleViewContext.tsx).
   - Add DNS + nginx config (see
     [subdomain-rollout.md](../runbooks/subdomain-rollout.md)).
3. Create a shell file in
   [shells/](../../interfaces/dashboard/src/shells/) with the
   persona's sidebar nav, hero, and landing route.
4. Add `layouts.ts` entries for every Pattern B feature
   (`alerts`, `live-map`, `overview`, `vehicle`).  CI
   (`check:layout-coverage`) enforces this.
5. Add `personaConfig.ts` entries (header copy, KPI priority, filter
   defaults) for the features that have them.
6. Backend: add the role to
   [`capabilities/iam/permissions.py::ROLE_PERMISSIONS`](../../capabilities/iam/permissions.py)
   and to
   [`capabilities/alerting/persona_mapping.py::_ROLE_TO_PERSONA`](../../capabilities/alerting/persona_mapping.py).
7. Backend: add the role to
   [`interfaces/api/deps.py::_VALID_VIEW_ROLES`](../../interfaces/api/deps.py)
   so Owner/Admin can preview as the new persona.

## Deferred / future work

Tracked here so future sessions don't re-invent these:

- **HR `DriverAssignmentPopover`** on Live Map — render the driver(s)
  currently assigned to a clicked vehicle.  Needs:
  - A new endpoint `GET /api/fleet/vehicles/{vehicle_id}/drivers`
    that returns active assignments from `driver_vehicle_assignments`.
  - A refactor of LiveMap's hardcoded side panel into a slot that
    Pattern B sections can write into (today the side panel is JSX
    inline in `LiveMap.tsx`, not section-driven).
  - The section then renders driver info in that slot when
    `selected != null` and the active view is HR.
- **Per-persona empty-state messaging** — positive feedback when an
  overlay has zero data ("No active faults today 🎉").  Needs a
  visible-text slot the imperative-leaflet overlays can write to
  (same side-panel-refactor blocker as above).
- **Live Map per-persona side panel content** — Pattern B-ifying the
  side panel itself so Fleet sees mechanical-context fields, Safety
  sees driver-behaviour context, Dispatch sees ETA, etc.  Same
  refactor scope.

These aren't critical-path work — the Live Map already differentiates
strongly between personas via the map overlays.  Side-panel
differentiation is a polish phase for when there's bandwidth.

## Naming: role words vs domain nouns

Role words (`fleet`, `safety`, `dispatch`, `hr`, `accounting`…) are
LIVE identifiers in this codebase — role strings, subdomains
(`fleet.4truck.us`), shells (`FleetShell`).  Every role-flavored word a
user sees ("Fleet Overview" vs "Safety Overview") is **generated from
the active view** by the persona system, never hardcoded.

Therefore:

- **Shared / wire data is named after the DOMAIN NOUN, never a
  persona.**  A key like `fleet.trucks` reads as "Fleet-role data" and
  makes the next developer ask "does Accounting get this?".  The
  correct name was `vehicles.trucks` — Vehicle is the role-neutral
  parent of truck and trailer.  (Real incident, 2026-07-16: the
  `/overview/stats` block shipped as `fleet.*` and was renamed.)
- **Persona words are correct ONLY in genuinely per-role artifacts** —
  `FleetHero.tsx`, `SafetyShell`, `LIVE_MAP_LAYOUTS` keys — things that
  exist once per role by design.
- **Renaming a wire key?** Ship the old key as a deprecated
  SAME-OBJECT alias plus a test asserting `alias == primary`, so stale
  SPA bundles survive the rollout.  Worked example:
  `/overview/stats` serves `vehicles` with a `fleet` alias
  (`features/overview/router.py`), pinned by
  `features/overview/tests/test_overview_stats_persona_filter.py`; consumers read
  `stats.vehicles ?? stats.fleet`.  Delete the alias (and the
  fallbacks) once no pre-rename bundle is live.

The sibling rule for AI tool descriptions: tools are called by every
role the permission gate allows, so their "USE THIS" examples must be
phrased role-neutrally too (no "my fleet" / "fleet efficiency").

## Anti-patterns to avoid

- **Don't hardcode a persona word into a wire key, schema field, or
  shared identifier.** See "Naming: role words vs domain nouns" above.
- **Don't reach into the bot for config UX.** Persona-group setup,
  account settings, etc. live on the dashboard.
- **Don't read `useShellConfig` inside a section.** Sections are
  persona-agnostic.  If your section needs persona info, the page
  wrapper must resolve it via `personaConfig.ts` and pass it down
  through `sectionProps`.
- **Don't gate UI on permissions alone when the gate is really
  about role workspace.** A Safety user has `can_faults=true` but
  shouldn't see the Faults card — gate on `kpiPriority.has('faults')`
  too (or just exclude it from `safety`'s priority array).
- **Don't split into multiple SPA codebases** unless you have ≥2
  frontend teams whose roadmaps don't overlap.  The one-codebase /
  N-subdomain model is the sustainable choice.
