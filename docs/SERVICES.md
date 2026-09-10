# System Services — the channels

Decided 2026-06-22 (the "Option C" pass), revised 2026-09-06. This is the
SSOT for the four **services** — **Alerts, the AI assistant, Reports,
and Mods**. They are **not features**: a service owns no data of its own;
it is a *channel* through which the role's features flow. Since 2026-09-06 a
service is **granted per role** like a feature (one View row each in the
matrix — `can_view_alerts`, `can_view_ai_assistant`, `can_view_reports`,
`can_view_mods`), so
an owner can withhold a channel from a role (a future broker role denied AI).
What flows *through* a granted channel is still decided by the role's
**feature** grants. For the toggleable feature taxonomy, see
[`FEATURES.md`](FEATURES.md).

## Service vs feature — two different architectures

| | Feature ([`FEATURES.md`](FEATURES.md)) | System service (this doc) |
|---|---|---|
| **Unit** | a `features/<x>/` leaf surface | a *hub* that aggregates contributions from many features |
| **Access** | owner-toggled per role (the Permissions page) | owner-toggled per role too (one View row: the channel) — content still follows the features |
| **Direction** | owns its own data + surface | consumes `alert.py` / `report.py` / `ai_tool.py` contributions **from** features |
| **Reader question** | "what may role X open?" | "what infra is always running, and how does content flow into it?" |

A service's access is a **stored grant** like any feature's — one View row
per service in the Services band that leads the Permissions page (it reads
top-down as the model: the channels, then what you grant, then what you
configure). Until 2026-09-06 the four service flags were computed by a
derivation step and hidden; that step is gone, the seeds carry what it
computed (every role holds all three; the inbox seed follows vehicle
visibility, so Recruiter starts without it — grantable, not hardcoded), and a
revocation sticks. The band's membership is the catalog's `kind: 'service'`
entries, pinned by `verbGrid.test.ts` against `SERVICE_ROW_KEYS`.

Those catalog entries exist because `featureCatalog.ts` doubles as the
route + nav registry. A service carries **no `tier`** — the type makes that
impossible (`CatalogEntry`'s service arm declares `tier?: never`), because a
value inside the tier union would claim services sit on an axis they don't.

## The six services

### 🔔 Alerts
- **Surface**: the Alerts inbox (dashboard) · bot `/alerts` · *My Notifications*.
- **Access**: `can_view_alerts`, granted per role (seeded wherever the role
  sees vehicles; Recruiter starts without it). A department switch never closes
  the inbox itself (it is `core`); it closes that department's alert
  TYPES, which the delivery and listing gates read from the resolved,
  masked permissions. Its **width** — every unit or
  the member's assigned trucks — is Team Management's answer (`unit_width`,
  the `alerts` pair), never a flag.
- **Content gate**: which alert **types** a role actually receives is gated
  per-feature by `capabilities/alerting/relevance.ALERT_TYPE_REQUIRED_PERM`
  (faults → `can_faults`, health → `can_health`, fuel → `can_fuel`,
  events / geofence / parking / maintenance → their flags). Disable a feature
  and its alerts drop out of the inbox — the inbox itself stays.
- **Contribution pattern**: each feature component owns an `alert.py` that
  self-registers via `@register_alert_source` (the scheduler loops the registry).

### 🤖 AI assistant
- **Surface**: chat + fleet summary (dashboard + bot).
- **Access**: `can_view_ai_assistant`, granted per role (seeded for every role).
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
- **Surface**: the Reports hub page (tabbed) + the scheduled-report subscription —
  a sub-feature with its own home, `capabilities/reporting/scheduled/` (API) and
  `features/reports/scheduled/` (dashboard); the bot's hourly sender is its Telegram adapter.
- **Access**: `can_view_reports`, granted per role (seeded for every role) —
  the hub **and** its scheduled-report subscription (the sub-feature in
  `capabilities/reporting/scheduled/`) open on the one verb.
- **Content gate**: which report **tabs** appear is gated per report **type** —
  and those types are genuine per-role **features** that live in the matrix
  under their **owning department**: **Risk Summary → Safety**
  (`can_view_risk_reports`, width Team Management's), **Cost Reports → Accounting**
  (`can_view_cost_reports`); the per-vehicle reports (Faults / Health / Fuel /
  Efficiency) live under Vehicles. So the report **engine** is a service; the
  report **types** are features.
- **Contribution pattern**: each feature component owns a `report.py`.

### 🔔 Notifications
- **Surface**: the top-bar bell and its dropdown, the `/notifications`
  centre, the `/notifications/preferences` settings (Telegram, email, push
  and per-category cadence), and delivery itself on every channel. Dashboard,
  bot DMs, push.
- **Access**: `can_view_notifications`, granted per role (seeded for every
  role, 2026-09-08; the field default carries every stored row that predates
  it). Withheld from a role: no bell, no centre, no settings, every
  notifications API door answers 403, and **nothing is delivered** to that
  person on any channel — broadcast (`_filter_recipients`) and targeted
  (`notify_user`) both ask the verb. The one exception is a **mandatory**
  category (security, billing): it passes the service gate as it passes a
  mute, because a payment problem must reach somebody.
- **Content gate**: what a person is notified ABOUT still follows the
  feature grants (a category's `requires_permission`, the alert-type map):
  the service is the channel, the features decide the content.
- **Alerts vs Notifications**: Alerts is the vehicle-alert inbox and its
  board (`can_view_alerts`); Notifications is the delivery machinery every
  notice rides, alerts included. A role with Alerts but without
  Notifications sees the board and receives no DM.

### 🎓 Tours
- **Surface**: the beacons on pages and the Tours library (`/tours`).
  Dashboard only.
- **Access**: `can_view_tours`, granted per role (seeded for every role,
  2026-09-10). A service for the reason Mods is: per person, no account
  data, withheld per role — without the grant no beacon shows, the library
  redirects and `/me/tour-signals` answers 403.
- **Content gate**: each tour still needs the feature it walks through
  (`features/tours/reachable.ts`); the service is the door, the features
  decide which tours exist behind it.

### 🎨 Mods
- **Surface**: the top-bar palette popover, the `/mods` page, the *Modifications*
  card on the profile. Dashboard only.
- **Access**: `can_view_mods`, granted per role (seeded for every role, 2026-09-08).
  Not a hub and owns no account data: everything it holds is **per person,
  per device** (`mods.*` preferences). It is a service for one reason — the
  owner can withhold it from a role the way any channel is withheld: without
  the grant every setting stays at its default, the popover and the doors
  are hidden, and `/mods` redirects — one row in the matrix, like the others.
  Nothing account-wide flows through it: no config verb, no company theme.
- **Content gate**: none — there is nothing role-specific inside it.

## The service verbs

```
can_view_alerts · can_view_ai_assistant · can_view_reports · can_view_mods
```

Plain grants, stored like any feature's, seeded for every role (the inbox
only where the role sees vehicles). Their legacy names — `can_alerts_all`,
`can_alerts_vehicle`, `can_ai_chat`, `can_digest` — are alias properties for
one release and die with the alias layer.

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
