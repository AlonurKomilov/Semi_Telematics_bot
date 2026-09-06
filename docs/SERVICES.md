# System Services — the always-on layer

Decided 2026-06-22 (the "Option C" pass). This is the SSOT for the three
always-on infrastructure **services** — **Alerts, the AI assistant, and
Reports**. They are **not features**: every role has them, always. An owner
never toggles a *service*; they only shape what *flows through* it by enabling
or disabling the underlying **features**. For the toggleable feature taxonomy,
see [`FEATURES.md`](FEATURES.md).

## Service vs feature — two different architectures

| | Feature ([`FEATURES.md`](FEATURES.md)) | System service (this doc) |
|---|---|---|
| **Unit** | a `features/<x>/` leaf surface | a *hub* that aggregates contributions from many features |
| **Access** | owner-toggled per role (the Permissions page) | always-on, **derived** — never a grantable row |
| **Direction** | owns its own data + surface | consumes `alert.py` / `report.py` / `ai_tool.py` contributions **from** features |
| **Reader question** | "what may role X open?" | "what infra is always running, and how does content flow into it?" |

A service's access is **computed, not stored**:
`capabilities/permissions/roles.derive_service_perms(fs)` runs as the **last**
step of every permission resolve (after the module mask) and overwrites the
service-surface flags. Nothing grants them; they lead the Permissions page's
per-role grid as a read-only **Services band** — first, because the page reads
top-down as the model: what every role always has, then what you grant, then
what you configure. The band's membership is the catalog's `kind: 'service'`
entries, pinned by `verbGrid.test.ts`.

Those catalog entries exist only because `featureCatalog.ts` doubles as the
route + nav registry. A service carries **no `tier`** — the type makes that
impossible (`CatalogEntry`'s service arm declares `tier?: never`), because a
value inside the tier union would claim services sit on an axis they don't.

## The three services

### 🔔 Alerts
- **Surface**: the Alerts inbox (dashboard) · bot `/alerts` · *My Notifications*.
- **Access (derived)**: every role **has** the inbox; only the **scope** is
  derived from the role's vehicle scope — `can_vehicle_all` → fleet-wide
  (`can_alerts_all`), otherwise own-vehicle (`can_alerts_vehicle`). The two are
  mutually exclusive. A role with *no* vehicle visibility at all gets no inbox
  (the unknown-role floor — every real role has vehicle scope).
- **Content gate**: which alert **types** a role actually receives is gated
  per-feature by `capabilities/alerting/relevance.ALERT_TYPE_REQUIRED_PERM`
  (faults → `can_faults`, health → `can_health`, fuel → `can_fuel`,
  events / geofence / parking / maintenance → their flags). Disable a feature
  and its alerts drop out of the inbox — the inbox itself stays.
- **Contribution pattern**: each feature component owns an `alert.py` that
  self-registers via `@register_alert_source` (the scheduler loops the registry).

### 🤖 AI assistant
- **Surface**: chat + fleet summary (dashboard + bot).
- **Access (derived)**: always on for every role (`can_ai_chat = True`).
- **Content gate**: each tool is gated by `TOOL_PERMISSIONS` to the data the
  role can **already** see — **a tool's access *is* its feature's access**.
  There is no AI-only permission: e.g. the engine-state lookup
  (`get_rolling_stopped`) follows Vehicles access (`can_vehicle_all`), exactly
  like `get_vehicle_detail` / `search_vehicles`. *(The off-pattern standalone
  `can_rolling_stopped` flag was removed 2026-06-22 — it was the one tool that
  carried its own gate instead of its feature's.)*
- **Contribution pattern**: each feature component owns an `ai_tool.py`
  registered in the AI tools registry.

### 📄 Reports
- **Surface**: the Reports hub page (tabbed) + the scheduled-report subscription.
- **Access (derived)**: the hub **and** its scheduled-report subscription
  (`can_digest = True`) are always on for every role.
- **Content gate**: which report **tabs** appear is gated per report **type** —
  and those types are genuine per-role **features** that live in the matrix
  under their **owning department**: **Risk Summary → Safety**
  (`can_view_risk_reports`, width Team Management's), **Cost Reports → Accounting**
  (`can_view_cost_reports`); the per-vehicle reports (Faults / Health / Fuel /
  Efficiency) live under Vehicles. So the report **engine** is a service; the
  report **types** are features.
- **Contribution pattern**: each feature component owns a `report.py`.

## The derived service flags (the only four)

```
DERIVED_SERVICE_FIELDS = {can_alerts_all, can_alerts_vehicle, can_ai_chat, can_digest}
```

Never stored, never a matrix row. `PUT /admin/permissions/roles` strips them
from the persisted override; migration 128 cleaned them from existing rows.
`derive_service_perms` is the single place they're set.

## hub ≠ service (the important nuance)

**"Hub"** is an *implementation* pattern — a registry + shared core that
collects one contribution from every feature: **Alerting, Reporting, AI,
Scorecards** (the four true `capabilities/`). **"System service"** is an
*access* model — always-on, derived.

Three of the four hubs became services. **Scorecards did NOT**: it's a
hub by implementation but remains a **gated feature** (`can_scorecard_all` /
`can_scorecard_vehicle` is still a matrix toggle), because driver-behaviour
data is role-sensitive. So Scorecards lives in [`FEATURES.md`](FEATURES.md),
not here. Hub describes *how it's built*; service describes *how it's reached*.

## What is NOT a service (so you don't look for it here)
- **The report types** (Risk Summary, Cost Reports) — features, in the matrix
  under Safety / Accounting.
- **Scorecards** — a hub-implemented but gated feature.
- **Permissions / Integrations / Storage** — **Administration**-tier features
  (own pages, `can_manage_*`).
- **Overview** — a **Shared**-tier feature (an aggregator *page*: persona-
  composed and gated by what it shows, not always-on infra).

## Why this is split from FEATURES.md
The two docs answer different questions and never duplicate a fact: FEATURES.md
owns the *toggleable feature taxonomy*; this doc owns the *always-on service
architecture*. Each fact has exactly one home. FEATURES.md's tier axis
(Personal / Shared / Role / Administration) deliberately has no member for
services — it cross-references here instead, so there is nothing to drift.
("System", the tier that used to hold this cross-reference, was retired
2026-07-30 once it had emptied into those four.)
