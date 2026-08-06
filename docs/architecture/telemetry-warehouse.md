# Telemetry warehouse — target architecture (SSOT)

Approved 2026-07-31 (owner + advisor). This document is the contract the
warehouse refactor arc implements. If code and this document disagree, one
of them is wrong — fix whichever, but never silently.

## The shape in one paragraph

Datasets are **declared per owning feature**; the machinery is **central and
generic**. `capabilities/data_lifecycle/` owns three sibling engines —
**ACQUIRE** (`ingest/`, new), **BUILD** (`rollups/`), **KEEP** (`retention/`)
— all discovering feature contributions via `_CONTRIBUTORS` module-name
strings + `make_discover` (never importing `features/`). Each feature's
`lifecycle.py` declares its full data lifecycle top-to-bottom: what it
acquires, how it tiers, what it keeps. `capabilities/warehouse/` was retired
at the end of the arc (2026-08-06) — see the as-built tree below.

## The tree — AS BUILT (Phase 5 landed 2026-08-06)

```
capabilities/
  data_lifecycle/               # the engines — feature-agnostic machinery
    _common.py                  #   fan-out + discovery
    ingest/                     #   ACQUIRE: registry, engine, watchdog,
                                #   router.py (/telemetry/warehouse-status,
                                #   URL unchanged through the move)
    rollups/   retention/       #   BUILD / KEEP (cascades carry an optional
                                #   ``reroll`` hook so capability machinery can
                                #   rebuild a stream without importing features)
    staleness.py  timegrid.py  catalog.py
  integrations/samsara/sync.py  # provider FETCHERS + shape adapters only
                                # (guard-enforced: never imports features/)
features/
  vehicles/
    lifecycle.py                # ACQUIRE + BUILD + KEEP declarations
    warehouse/                  # the feature's OWN warehouse logic
      aggregator.py             #   tier aggregation (+ registers as reroll)
      readers.py                #   warehouse-first reads, age-based fallback
      service.py                #   health/weather/efficiency read facade
  events/lifecycle.py           # safety_event_log dataset
  drivers/lifecycle.py          # driver_efficiency dataset
  geofencing/lifecycle.py       # geofence-definitions cache dataset
adapters/storage/
  warehouse/                    # one mixin file per stream — the package IS
    vehicles.py  safety.py      # the prefix (same rule as the Postgres
    drivers.py   geofences.py   # schema); combined WarehouseMixin exported
    aggregates.py  ledgers.py   # at the old dotted path
    _util.py
  ops_runs.py                   # OpsRunsMixin: scheduler_jobs + retention_runs
                                # (platform-wide, so NOT in the warehouse family)
```

`capabilities/warehouse/` is GONE (the arc-end state).  Tolerated
debt, cleaned when those roots get their own audit arc:
capabilities/{reporting,scorecards,ai} import the vehicles read
facade directly (beside the pre-existing permissions/vehicle_scope
case) — legal under the guard table, listed here so it is chosen,
not accidental.

## Naming: stream + grain (the two words, kept separate)

One dataset = one STREAM name; resolution is a GRAIN label, never part
of the name.  The rule that decides physical tables: **shape decides
the table, grain is a label** — rows with identical columns share one
table with a grain column; rows of different kinds (a *sample* is a
moment, an *aggregate* is a period) get their own table.  The clean
vocabulary lives in the declarations and in the `vehicle_timeline`
VIEW (migration 182), which presents all five grains as one queryable
surface.

**The `warehouse` schema (decided 2026-08-03, pre-customer window):**
the whole family — 13 tables + the view — lives in a dedicated
Postgres schema, moved by the operator window script
(docs/runbooks/warehouse-schema-move.md) and mirrored by idempotent
migration 183 for fresh installs.  Four names were normalized in the
same move: `driver_efficiency_daily → driver_efficiency` (grain out
of the name), `aggregate_weather_snapshot → weather_snapshot`,
`aggregate_efficiency_snapshot → efficiency_snapshot`, and
`warehouse_ingest_orphans → ingest_orphans` (the schema IS the
prefix).  `safety_event_log` keeps its name — "safety event" is the
industry's own noun here, not a role word.  Unqualified queries
resolve via the pool search_path `public,warehouse` (public FIRST so
unqualified CREATEs land in public; the shadow-orphan guard test in
tests/test_source_ts.py asserts no warehouse name ever reappears
there).  Explicit `warehouse.` qualification of the ~900 code
references is a later, CI-guarded follow-up — never part of cutover.

| Stream | Grain | Kind | Physical table | Kept |
|---|---|---|---|---|
| vehicle.timeline | live | sample | `vehicle_state` | until departed |
| vehicle.timeline | minute | sample | `vehicle_state_snapshot` | 7 d |
| vehicle.timeline | hour | aggregate | `vehicle_telemetry` (`granularity='hourly'`) | 90 d |
| vehicle.timeline | day | aggregate | `vehicle_telemetry` (`granularity='daily'`) | 730 d |
| vehicle.timeline | week | aggregate | `vehicle_telemetry` (`granularity='weekly'`) | 1825 d |

**Asset grain surfaces (migration 185, owner decision 2026-08-03):**
each ASSET stream exposes one READ view per grain, named
`<stream>_<grain>` — the warehouse's official addressing surface:

| grain | state stream | health stream |
|---|---|---|
| live | `warehouse.vehicle_state_live` | `warehouse.vehicle_health_live` |
| minute | `warehouse.vehicle_state_minute` | `warehouse.vehicle_health_minute` |
| hour | `warehouse.vehicle_state_hour` | `warehouse.vehicle_health_hour` |
| day | `warehouse.vehicle_state_day` | `warehouse.vehicle_health_day` |
| week | `warehouse.vehicle_state_week` | `warehouse.vehicle_health_week` |

Two naming layers, both deliberate: PHYSICAL tables follow the
template (never a grain in the name — shape decides the table);
SURFACE views are named by grain because grain-addressing is their
entire job.  Readers use the surfaces; writers use the physical
tables (upserts cannot ride views).  This is interface-first: if the
physical layer is ever restructured (e.g. per-grain tables), only the
view bodies change — no consumer moves.  `vehicle_timeline` remains
the cross-grain surface (all five grains, one query).

The minute grain samples at the live ingest's own 60-second cadence
(pre-2026-08 history is 5-minute spaced; duty math is gap-based and
cadence-independent, so both spacings compute correctly).  Stored
`granularity` values (`hourly/daily/weekly`) are legacy wire values —
the view maps them; new declarations use the grain vocabulary.  Every
NEW dataset adopts this scheme from day one: stream named after the
domain, grain declared separately.

**Table-name template (every warehouse table):**
`<domain-noun>_<subtask>[_<kind>]` — `vehicle_fault_snapshot` is the
model citizen.  Never a grain in the name (grain is a label/column),
never a role word (PERSONA.md), never a `warehouse_` prefix (the
schema is the prefix).  New tables are born in the `warehouse`
schema.  What a name still can't say, the database says:
`capabilities/data_lifecycle/catalog.py` stamps every registered
table with a `COMMENT` (family · dataset key · owner · grain note),
generated from the ingest registry at boot so it cannot drift.
Developers see it in psql `\dt+` and in every DB GUI's table tree.

**Three categories of warehouse data (decided 2026-08-03) — only
assets earn the grain ladder:**

- **Asset** — continuous gauges whose history is irreplaceable (the
  provider cannot hand it back): position, speed, fuel, odometer,
  health gauges.  Assets get the full grain cascade.
- **Log** — events at full fidelity (`safety_event_log`,
  `vehicle_fault_detail`); never resampled, their COUNTS ride the
  aggregate tiers (`harsh_event_count`, `fault_count_eod`).
- **Cache** — re-fetchable convenience kept for speed
  (`weather_snapshot`, `geofence_definitions`, the current-state
  fault/health/efficiency snapshots); one current row, no history
  pretensions, no ladder.

A new dataset declares which it is by how it's stored; "should this
have hour/day/week?" is answered by the category, not by taste.

**Time-keeping rule** (`capabilities/data_lifecycle/timegrid.py`):
a row's time label and its observation truth are two different
questions with two different columns.  The label sits ON the grain's
grid — sample tiers floor `captured_at` to the slot via the shared
`floor_to_slot` (minute rows say `07:26:00`, never `07:26:13`);
aggregate tiers label the bucket start (hour `:00:00`, day
`YYYY-MM-DD`, week its ISO Monday).  The moment the provider's sensor
actually sampled rides in `source_ts` (Contract 2) and is never
rounded.  Writers of time-series rows ride wall-aligned **cron**
triggers, not intervals — an interval fires N seconds from process
boot, so every restart mints a new second-offset and writes the
deploy history into the data.  The `live` grain is exempt: it is one
current row per vehicle carrying provider time, not a series.

## Contract 1 — Ingest registration

```python
@dataclass(frozen=True)
class IngestDataset:
    key: str                  # "vehicles.state" — domain noun, never a role word
    owner: str                # feature id per docs/FEATURES.md
    job_id: str               # legacy id verbatim ("warehouse_vehicle_state")
    cadence: dict             # same spec as RollupStage
    run: Callable[[int], Awaitable[int]]   # (account_id) -> rows written
    tables: tuple[str, ...]   # physical tables written (watchdog + RLS audit read this)
    freshness_sla_min: int    # watchdog alerts past this source-data age
    expect_rows: bool = True  # False for sparse feeds (faults)
```

The engine fans out per active account (carrying the integration-connected +
capability-toggle gate the hand-wired jobs use today) and records every run
into `ingest_runs (dataset_key, account_id, rows_written, max_source_ts,
ran_at)`. The scheduler generates ingest jobs from the registry exactly as it
generates rollup jobs from `RollupCascade`. The watchdog reads the same
declarations: zero-row streaks where `expect_rows`, and `max_source_ts` age
vs `freshness_sla_min`. A dataset can no longer die silently.

**Dataset → owner map** (settled 2026-07-31):

| dataset | tables | owner |
|---|---|---|
| vehicles.state / health / faults | vehicle_state, vehicle_health_snapshot, vehicle_fault_snapshot, vehicle_fault_detail | features/vehicles |
| vehicles.weather / efficiency | weather_snapshot, efficiency_snapshot | features/vehicles (account-wide *vehicle* aggregates — previously unowned) |
| events.safety | safety_event_log | features/events (docs/FEATURES.md "that's platform" line is superseded) |
| drivers.efficiency | driver_efficiency | features/drivers |
| geofencing.definitions | geofence_definitions | features/geofencing |

**Billing side-effect (settled):** `ingest_vehicle_state` today ends by
calling billing's `sync_billing_quantity` — a platform-money side-effect
that is neither fetch nor upsert. It does NOT ride the dataset `run`.
It moves to a billing-owned scheduled step reading `vehicle_state` on its
own cadence (billing change ⇒ advisor/review-gated when implemented).

## Contract 2 — Staleness

Two timestamps, uniform meaning on every warehouse table:

- `source_ts` (new, nullable) — when the PROVIDER's sensor actually sampled.
  Ingest maps provider markers (odometer_time, gps time, …) into it. Roll-up
  stages propagate `max(source_ts)` of their inputs and **never mint it**.
  The minute snapshot keeps the old `source_ts` for unchanged rows — or skips
  dead rows entirely. NULL renders as "age unknown", never as fresh.
- `captured_at` — **semantics are frozen per table as they are today** and
  documented here: on `vehicle_state` it currently holds PROVIDER time (the
  billing activity window and freshness cards depend on that); on snapshot
  tables it holds OUR write time. We never flip a column's meaning in place —
  readers migrate to `source_ts` instead.

Readers use one shared `is_stale(source_ts, sla)` helper; live-Samsara
fallback fires on AGE, not on table-emptiness. Guard test: every table
listed in any registered dataset must have `source_ts`.

## Contract 3 — Identity

**Naming decision (settled — collision found by the conformance sweep):**
every warehouse table already has `vehicle_id` = the SAMSARA external id.
That wire name never changes. The registry identity gets a NEW column
**`registry_id`** (BIGINT, nullable, = Postgres `vehicles.id`; the vehicles
API already exposes this name). Resolution happens at ingest by
`(account_id, samsara external id)` — never by name. Name is a display
label only. Unmatched identities land in `ingest_orphans`
(dataset_key, account_id, external_id, name, count, first/last_seen),
surfaced by the watchdog. Consumers join on `(account_id, registry_id)`.

Tenancy repair rides this contract: the five tables keyed without
`account_id` (vehicle_state, vehicle_health_snapshot,
vehicle_fault_snapshot, weather_snapshot, geofence_definitions —
plus safety_event_log's global `UNIQUE(samsara_event_id)`) get
account-scoped keys via CONCURRENTLY-built unique indexes + constraint swap,
and their upserts stop overwriting `account_id` on conflict.

## Invariants (CI-guarded)

1. Production **table names never change**; `vehicle_id` keeps meaning
   Samsara external id.
2. Scheduler **job_ids stay verbatim** (operator console keys on them) —
   guarded by a job-id snapshot test.
3. `capabilities/` never imports `features/` — layer-boundary guard gains
   entries for `capabilities/data_lifecycle` (lands green today) and, at
   arc end, `capabilities/integrations`.
4. API URLs byte-identical through every move (route-parity tests, the
   drivers-split precedent).
5. Migrations online-only: `ADD COLUMN NULL` + `CREATE [UNIQUE] INDEX
   CONCURRENTLY`; never CREATE INDEX inside platform_schema.py's
   CREATE-TABLE block.

## Phase routing

Fixes land in their natural phase — never early, never twice:

- **Phase 1 (rescue):** outage-window re-aggregation + forced Samsara
  backfill; odometer jump/reset guard; re-roll repair mode; restore the
  missing `uniq_vehicle_state_account_company_name` index.
- **Phase 2 (root causes):** engine-state ingest, hour-23 race, fuel
  persist, `source_ts` columns + propagation (Contract 2), fetcher/upsert
  split in sync.py, `ingest_runs` + registry + engine (first datasets
  registered).
- **Phase 3 (identity):** `registry_id` columns + ingest resolver +
  orphan quarantine (Contract 3); consumers move off name joins (the sweep
  holds the full site list); tenancy key swaps.
- **Phase 4 (hardening):** watchdog as alert source; misfire/DST/cron
  fixes; staleness checks in readers; RLS coverage incl.
  vehicle_state_snapshot.
- **Phase 5 (mechanical re-homing):** storage split, aggregator + readers
  move, `capabilities/warehouse/` deleted, guards tightened. Method/function
  names unchanged ⇒ re-points are one-line import edits.

The full violation inventory (113 items, per-file, with fix options) is a
working paper from the 2026-07-31 conformance sweep — session artifact, not
committed; this document carries everything durable.
