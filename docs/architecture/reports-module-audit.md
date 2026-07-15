# Reports Module — Comprehensive Audit

> **As of 2026-06-03.** Evidence-based inventory of every report-shaped
> surface across the 4truck codebase. All paths are relative to the repo
> root (`/home/abcdev/projects/Semi_Telematics_bot`). Cross-references
> to [permissions-feature-matrix.md](permissions-feature-matrix.md) are
> used in lieu of restating gate work already documented.

### Sources scanned

- `capabilities/reporting/*.py` (19 files, ~6.0k LOC)
- `interfaces/api/routes/reports.py`, `safety.py`, `ai.py`, `user.py`, `work_orders.py`, `maintenance.py`
- `interfaces/dashboard/src/pages/reports/*.tsx`, `pages/work-orders/Reports.tsx`
- `interfaces/dashboard/src/shells/nav/*.ts` (6 persona nav files), `router.tsx`, `components/shell/routeRegistry.ts`
- `interfaces/bot/reports.py`, `auto_reports.py`, `vehicles.py`, `keyboards.py`, `scheduler.py`, `callbacks/__init__.py`
- `adapters/storage/settings.py`, `migrations.py`, `schema.py`
- `capabilities/ai/tools/*.py`, `capabilities/ai/intelligence.py`
- `capabilities/notifications/email.py`
- `capabilities/alerting/dnd.py` (shift-report digest path)

---

## 1. Inventory Matrix — Reports × Delivery Channels

| # | Report | Dashboard page | API endpoint | PDF | CSV | Scheduled bot delivery | AI tool | Bot command | Permission gate |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Faults (active DTCs) | `/reports` (Faults tab) `Reports.tsx:152` | `GET /reports/faults` `reports.py:42` | `generate_fault_report_pdf` | `generate_fault_csv` | yes — `auto_reports.py:193` | `get_vehicle_faults` `ai/tools/faults.py:12` | redirect `bot/reports.py:66` | `can_faults` |
| 2 | Critical Faults (subset) | — | — | `generate_critical_report_pdf` | — | — | `get_vehicle_faults(critical_only)` | called from `bot/vehicles.py:226` | `can_critical` flag REMOVED 2026-06-03 |
| 3 | Fuel & DEF levels | `/reports` (Fuel tab) | `GET /reports/fuel-levels` + legacy `/reports/fuel` | `generate_fuel_report_pdf` | `generate_fuel_csv` | yes — `auto_reports.py:210` | `ai/tools/fuel.py` | redirect `bot/reports.py:84` | `can_fuel` |
| 4 | Vehicle Health | `/reports` (Health tab) | `GET /reports/health` | `generate_vehicle_health_pdf` | `generate_health_csv` | yes — `auto_reports.py:218` | `ai/tools/health.py` | redirect `bot/reports.py:122` | `can_health` |
| 5 | Fleet Efficiency | `/reports` (Efficiency tab) | `GET /reports/efficiency` | `generate_fleet_efficiency_pdf` | `generate_efficiency_csv` | yes — `auto_reports.py:227` | `get_efficiency_summary` | redirect `bot/reports.py:103` | `can_efficiency` ∨ `can_vehicle_all` |
| 6 | Camera Check | — | — | `generate_camera_check_pdf` | `generate_camera_check_csv` | yes — `auto_reports.py:235` | `ai/tools/camera.py` | callback-only | `can_digest` (via auto-reports menu) |
| 7 | Stakeholder Risk Summary | `/reports/risk-summary` | `GET /reports/risk-summary` + `/risk-summary/me` | `generate_risk_summary_pdf` | `generate_risk_summary_csv` | no | — | redirect `bot/reports.py:161` | `can_risk_report_all` ∨ `_own` |
| 8 | Vehicle Detail PDF | — | — | `generate_vehicle_detail_pdf` | — | — | — | `bot/vehicles.py:142` | `can_faults` (bot) |
| 9 | Weather Overlay PDF | — | — | `generate_weather_pdf` | — | — | — | redirect `bot/reports.py:142` | dead — see §4 |
| 10 | Truck PDF | — | — | `truck_pdf.py` exists | — | — | — | **no callers** — dead |
| 11 | Shift Report PDF | — | — | `generate_shift_report_pdf` | — | DnD-end digest `dnd.py:326` | — | — | DnD subscriber list |
| 12 | DOT Compliance Binder | — | `GET /maintenance/dot-binder/...` | `render_dot_binder_pdf` + `build_dot_binder` | — | — | — | — | `can_maintenance_all` |
| 13 | Cost Reports (Work Orders) | `/cost-reports` `pages/work-orders/Reports.tsx` | `GET /work-orders/reports/{per-vehicle,per-task-type,per-vendor,summary,monthly-trend}` `work_orders.py:524-625` | — | client-side CSV `Reports.tsx:178` | no | — | — | `can_maintenance_all` (**overload**) |
| 14 | AI Fleet Summary | `/ai/chat?tab=briefing` (`/ai/summary` is a redirect stub) | `POST /ai/summary` `ai.py:210` | — | — | no | — | — | `can_faults` ∨ `can_vehicle_all` ∨ `_own` |
| 15 | Scorecards Summary (KPI strip) | `/safety` family | `GET /scorecards/summary` `safety.py:805` | — | — | no | — | — | `can_scorecard_all` ∨ `_own` |

### Risk Summary audience variants (`capabilities/reporting/audiences.py:71-153`)

| Audience | Label | Driver PII | Video links | Sections cut vs default |
|---|---|---|---|---|
| insurance | Insurance Underwriter Packet | ✓ | ✓ | – |
| owner | Fleet Owner Internal Review | ✓ | ✓ | – |
| broker | Broker of Record Packet | redacted | ✗ | drops driver_info, trend, maintenance |
| auditor | DOT / Compliance Auditor Packet | ✓ | ✓ | drops comparison |
| payroll | Safety-Pay Evidence Packet | ✓ | ✗ | driver-only; drops vehicle_info, maintenance, comparison, data_sources |

---

## 2. Permission Gating Matrix

| Surface | File:line | Gate(s) | Notes |
|---|---|---|---|
| `/reports` landing | `router.tsx:167` | `can_faults` | Sidebar shows it to every persona (`permission: null` in all 6 nav files); roles without `can_faults` 403. Matrix row 47. |
| `/reports/scheduled-reports` | `router.tsx:168` | `can_digest` | Guard added 2026-06-03 (Issue 7 fix). |
| `/reports/risk-summary` | `router.tsx:172` | `can_risk_report_all` ∨ `_own` | Sidebar matches in default/fleet/safety/hr; correctly omitted in dispatch/accounting. |
| `/cost-reports` | `router.tsx:195` | `can_maintenance_all` | **Overload** — `permissions.py:288-294` admits a TODO for `can_cost_reports`. |
| `GET /reports/faults` | `reports.py:42` | `can_faults` | – |
| `GET /reports/fuel-levels` | `reports.py:73` | `can_fuel` | – |
| `GET /reports/fuel` (legacy) | `reports.py:118` | `can_fuel` | Hidden from schema. |
| `GET /reports/health` | `reports.py:127` | `can_health` | – |
| `GET /reports/efficiency` | `reports.py:147` | `can_efficiency` ∨ `can_vehicle_all` | Wider than the landing page gate. |
| `GET /reports/export` | `reports.py:196` | any-of + per-type re-check `reports.py:212` | Two-layer enforcement. |
| `GET /reports/risk-summary` | `reports.py:256` | `can_risk_report_all` ∨ `_own` + own-scope subject check `reports.py:290-299` | Defense-in-depth. |
| `GET /reports/risk-summary/me` | `reports.py:349` | same any-of | Self-service shortcut. |
| `GET/PUT/DELETE /user/scheduled-reports` | `user.py:182,201,221` | `can_digest` | Legacy `/subscriptions` aliases inherit guard via shared handler. |
| `GET /work-orders/reports/*` | `work_orders.py:524-625` (5 endpoints) | `can_maintenance_all` | All five carry same gate. |
| `GET /maintenance/dot-binder/...` | `maintenance.py:1232` | `can_maintenance_all` | – |
| `POST /ai/summary` | `ai.py:210` | `can_faults` ∨ `can_vehicle_all` ∨ `_own` | – |
| Bot `cmd_auto_reports` (and wizard steps) | `auto_reports.py:62,96` | `can_digest` | Bot-side. |
| Bot redirects (`cmd_faults`, `_fuel`, `_health`, `_efficiency`, `_weather`, `_risk_report`) | `bot/reports.py:66-172` | none beyond `_require_registered` | Pure deep-link stubs. |

---

## 3. Duplication Findings

### 3.1 Sidebar over-population

Four distinct "Reports" sidebar items across personas (Reports, Risk Summary, Scheduled Reports, Cost Reports), plus a fifth re-use of the `reports_group` i18n label by `accountingNav.ts` for `/costs` entries.

| Persona | Reports | Risk Summary | Scheduled Reports | Cost Reports | Other in group |
|---|---|---|---|---|---|
| default | ✓ | ✓ | ✓ | ✓ | – |
| fleet | ✓ | ✓ | ✓ | ✓ | – |
| safety | ✓ | ✓ | ✓ | ✗ | – |
| dispatch | ✓ | ✗ | ✓ | ✗ | – |
| hr | ✓ | ✓ | ✓ | ✗ | – |
| accounting | ✓ | ✗ | ✓ | ✓ | Fuel Costs + CPM under same `reports_group` |

`routeRegistry.ts:51-60` (command palette) carries a third "Reports group items" registry on top of the nav files.

### 3.2 PDF generator cohesion

`capabilities/reporting/pdf_base.py` (1310 LOC) exports **74 names** via `__all__`. Eight of nine `*_pdf.py` files use `from .pdf_base import *`; `risk_summary_pdf.py:31` and `dot_binder_pdf.py:35` use explicit imports.

| File | Lines | Imports from base | Aggregator? |
|---|---|---|---|
| `fault_pdf.py` | 243 | `*` | uses base `compute_stats` |
| `efficiency_pdf.py` | 294 | `*` | own |
| `fuel_pdf.py` | 288 | `*` | own |
| `health_pdf.py` | 238 | `*` | own |
| `camera_pdf.py` | 144 | `*` | none |
| `shift_pdf.py` | 305 | `*` | own |
| `vehicle_pdf.py` | 242 | `*` | n/a |
| `truck_pdf.py` | 242 | `*` | **dead** |
| `weather_pdf.py` | 215 | `*` | n/a |
| `risk_summary_pdf.py` | 545 | explicit | uses risk_profile |
| `dot_binder_pdf.py` | 562 | explicit | uses `build_dot_binder` |

Layout chrome (header, footer, TOC, summary dashboard, fleet status grid, vehicle card, drive/idle bar) **is** DRY in `pdf_base.py`. The star-import pattern, however, is brittle and was abandoned for the two newest generators — tacit consensus the pattern is wrong, never followed up.

### 3.3 Data-assembly drift (api vs bot)

`auto_reports._generate_report_pdf` (`auto_reports.py:182-271`) re-implements the data-fetch step with a different upstream for every report vs the API:

| Report | API upstream | Bot upstream | Same code path? |
|---|---|---|---|
| Faults | `_wh.get_vehicles_with_faults` w/ Samsara fallback (`reports.py:61`) | `_svc_vehicles_with_faults` (`auto_reports.py:194`) | **no — bot bypasses warehouse** |
| Fuel | `_wh.get_current_vehicles` w/ fallback (`reports.py:92`) | `_svc_fleet_overview` (`auto_reports.py:211`) | **no** |
| Health | `_svc_vehicle_health` (`reports.py:135`) | `_svc_fleet_overview` (`auto_reports.py:220`) | **no** |
| Efficiency | `_svc_fleet_efficiency` (`reports.py:156`) | `_svc_fleet_overview` (`auto_reports.py:228`) | **no** |

This is the single biggest correctness risk in the module — a user can get a different vehicle list from the scheduled PDF and the dashboard download for the same point in time.

### 3.4 Subscription naming residue (post-rename)

- `SubscriptionRequest` Pydantic model (`user.py:166`)
- Response envelope `{"subscription": ...}` (`user.py:198,220`)
- TS type `Subscription` (`ScheduledReports.tsx:6`)
- Table `digest_subscriptions` (`schema.py:134`), methods `subscribe_digest_ext` / `unsubscribe_digest` / `get_digest_subscription`
- Flag `can_digest` retained
- Legacy `/subscriptions` API aliases with "delete after one release cycle" comment
- Bot `keyboards.py:500-505` `type_labels` map omits Camera Check (subscribed user sees raw `camera`); `auto_reports.py:49-55` REPORT_TYPES has it.

### 3.5 Two `subscribe_digest*` methods

`subscribe_digest(user_id, frequency, send_hour)` at `settings.py:44` is unused — every production caller uses `subscribe_digest_ext` at `settings.py:87`.

### 3.6 Five places define "the report list"

- `interfaces/api/routes/reports.py:168-193` (`EXPORT_TYPES`)
- `interfaces/bot/auto_reports.py:49-55` (`REPORT_TYPES`)
- `interfaces/bot/keyboards.py:500-505` (`type_labels`)
- `interfaces/dashboard/src/pages/reports/Reports.tsx:26-31` (`ALL_TABS`)
- `interfaces/dashboard/src/pages/reports/ScheduledReports.tsx:10-16` (`REPORT_TYPES`)

---

## 4. Architecture Observations

### 4.1 Strengths

1. **`pdf_base.py` for layout primitives** — adding a new PDF doesn't re-invent header/footer/TOC/grid/card.
2. **`transformers.py`** — clean `simplify_*` helpers consumed by both API and (potentially) bot for identical payload shapes.
3. **`audiences.py`** — textbook strategy pattern; new audience = one dict entry.
4. **Risk Summary pipeline is correctly separated** — `risk_profile.py` is pure data, `risk_summary_pdf.py` is pure layout.
5. **Two-layer permission check on `/reports/export`** (`reports.py:202` decorator + `reports.py:212` per-type) — reusable pattern.
6. **Cost Reports backend** computes prior-period deltas server-side (`work_orders.py:595`) so the dashboard never divides-by-zero.
7. **Defense-in-depth on Risk Summary** (`reports.py:290-299`) — own-scope callers cross-checked against truck list.
8. **Audit logging on Risk Summary export** (`reports.py:327-338`) — every PDF/CSV recorded with audience + window; failure to log doesn't break export.

### 4.2 Weaknesses

1. Cross-channel data drift (§3.3).
2. No single "report registry"; five reconstructions (§3.6).
3. `pdf_base.py` star-import pattern abandoned mid-stream (§3.2).
4. Email transport not wired into reports — Telegram is the only delivery channel (`auto_reports.py:331`); users without `telegram_id` get nothing.
5. Legacy `subscribe_digest` (4-arg) shadowed by `_ext`; never used.
6. Cost Reports page lives under `pages/work-orders/` not `pages/reports/`, despite navigating to `/cost-reports` and being grouped under "Reports".
7. `truck_pdf.py` (242 LOC) and `weather_pdf.py` (215 LOC) effectively dead.
8. Bot redirect deep-link paths don't exist — `bot/reports.py:79,98,117,136` point to `/reports/faults`, `/reports/fuel`, `/reports/efficiency`, `/reports/vehicle-health`; `router.tsx:167-172` only has `/reports`, `/reports/scheduled-reports`, `/reports/risk-summary`.

### 4.3 Pipeline traceability

| Report | DB / source | Service | Formatter | Delivery |
|---|---|---|---|---|
| Faults (API) | warehouse + Samsara fallback | `_wh.get_vehicles_with_faults` | `simplify_fault` → `generate_fault_report_pdf` | StreamingResponse |
| Faults (bot) | Samsara | `_svc_vehicles_with_faults` | inline + `generate_fault_report_pdf` | `bot.send_document` |
| Risk Summary | scorecard snapshots + safety events + maintenance | `build_risk_profile` | `generate_risk_summary_pdf` + `AudienceConfig` | StreamingResponse + audit log |
| Cost Reports | `work_orders` + parts + tasks | `cost_by_vehicle/...` in tenant_db | client-side React + recharts | dashboard + client CSV |
| DOT Binder | maintenance + work orders | `build_dot_binder` | `render_dot_binder_pdf` | StreamingResponse |
| AI Summary | Samsara via `ai.build_context` | `ai.generate_summary` | LLM | JSON `{summary, suggestions, usage}` |

Risk Summary and DOT Binder are the **best-shaped** (clean DB → assembler → formatter → response). Faults/Fuel/Health/Efficiency are the **drift-prone** (same formatter, two upstreams).

---

## 5. Anomalies & Accuracy Concerns

| # | Concern | Severity | File:line |
|---|---|---|---|
| A1 | Bot redirect paths don't exist as dashboard routes | 🔴 | `bot/reports.py:79,98,117,136` vs `router.tsx:167-172` |
| A2 | Faults/Fuel/Health/Efficiency PDFs from bot use different upstream than API | 🔴 | `auto_reports.py:194,211,220,228` |
| A3 | Cost Reports gated by `can_maintenance_all` (overload) | 🟡 | `router.tsx:195`, TODO `permissions.py:288-294` |
| A4 | `/reports` landing gated by `can_faults` but sidebar shows it to all personas | 🟡 | `router.tsx:167`, 6 nav files |
| A5 | `truck_pdf.py` (242 LOC) unreferenced | 🟡 | `capabilities/reporting/truck_pdf.py` |
| A6 | `weather_pdf.py` (215 LOC) effectively unreferenced | 🟡 | `__init__.py:8,33` |
| A7 | `subscribe_digest` legacy form has no callers | 🟢 | `settings.py:44,87` |
| A8 | `SubscriptionRequest`/`Subscription` types kept post-rename | 🟢 | `user.py:166,198,220`; `ScheduledReports.tsx:6` |
| A9 | `digest_subscriptions` is one-sub-per-user (UNIQUE user_id) — confirm intent | 🟡 | `schema.py:134-159` |
| A10 | `auto_reports_menu_kb` label map missing Camera Check | 🟢 | `keyboards.py:500-505` vs `auto_reports.py:49-55` |
| A11 | `can_critical` flag — **REMOVED 2026-06-03** | ✅ done | – |
| A12 | `/reports/efficiency` API wider than `/reports` page gate | 🟢 | `reports.py:151` |
| A13 | `routeRegistry.ts:51-60` repeats nav data (third source of truth) | 🟢 | `components/shell/routeRegistry.ts` |
| A14 | "Reports" group label re-used for /costs entries on Accounting nav | 🟢 | `accountingNav.ts:31-37` |
| A15 | 90s timeout hard-coded client-side on Risk Summary; backend has no cap | 🟢 | `RiskSummary.tsx:136` |
| A16 | Email transport unused for reports; users without `telegram_id` have no delivery | 🟡 | `auto_reports.py:331` |

Severity legend: 🔴 data integrity / broken UX · 🟡 tech debt with user-visible smell · 🟢 cleanup.

---

## 6. Recommended Refactors

### Tier 1 — Fix what's broken (S/M)

1. **Fix bot redirect paths** (S, ~30 min) — point `bot/reports.py:79,98,117,136` to `/reports?tab=…` and honour `?tab=` in `Reports.tsx:139`.
2. **Single data-fetch path for the 4 core reports** (M, ~1 day) — extract `report_data.fetch_for_pdf(account_id, report_type)` used by both `reports.export_report` and `auto_reports._generate_report_pdf`.
3. **Add `can_cost_reports` flag** (S, ~2 h + matrix update) — split off `can_maintenance_all` per existing TODO.

### Tier 2 — DRY + dead code (M)

4. **Single report registry** (M, ~1 day) — one module defining `{key, label, emoji, icon, gate}`; bot menu, dashboard tabs, ScheduledReports picker, AI tool descriptions, and `EXPORT_TYPES` read from it.
5. **Migrate remaining `*_pdf.py` off `from .pdf_base import *`** (M, ~3 h) — explicit imports throughout; drop the 74-entry `__all__`.
6. **Delete `truck_pdf.py` and `weather_pdf.py`** (S, ~30 min).
7. **Delete `subscribe_digest` (4-arg)** (S, ~15 min).

### Tier 3 — Naming / consistency (S)

8. **Finish "Scheduled Reports" rename** (S, ~1 h) — rename `SubscriptionRequest` → `ScheduledReportRequest`, envelope key, TS type; drop `/subscriptions` aliases.
9. **Move Cost Reports page to `pages/reports/CostReports.tsx`** (S, ~10 min) — one-line `router.tsx:81` change.
10. **Add Camera Check to `keyboards.py` `type_labels`** (S, ~5 min).

### Tier 4 — Wider redesign (L)

11. **Multi-subscription support** (L, ~3-4 days) if A9 is a real requirement — schema migration UNIQUE(user_id) → (user_id, report_type), API/UI to manage list.
12. **Email channel for scheduled reports** (L, ~2 days) — `email` boolean on `digest_subscriptions`, send PDF via `send_email`, fall back to Telegram.

---

## 7. Cross-platform Coherence

| Report | Dashboard | Bot scheduled | AI tool | Drift risk |
|---|---|---|---|---|
| Faults | warehouse + Samsara fallback | live Samsara | live Samsara | 🔴 high |
| Fuel | warehouse + fallback | fleet overview snapshot | live Samsara | 🔴 high |
| Health | health service | fleet overview snapshot | live Samsara | 🟡 medium |
| Efficiency | efficiency service | fleet overview snapshot | live Samsara | 🟡 medium |
| Risk Summary | scoring + safety + maintenance | — | — | 🟢 low |
| Cost Reports | aggregate SQL | — | — | 🟢 low |
| DOT Binder | aggregate SQL | — | — | 🟢 low |

---

## 8. Counts Summary

- **Distinct reports:** 15 (Risk Summary supports 5 audience variants → 19 effective output shapes).
- **PDF generators:** 11.
- **CSV generators:** 7.
- **Report-related API endpoints:** 18.
- **Sidebar entries (across 6 personas):** 21.
- **Permission flags gating report surfaces:** 7.
- **Dead PDF generators:** 2 (~457 LOC).
- **Legacy API aliases retained:** 4.
- **Channels per core report:** 4-5.
