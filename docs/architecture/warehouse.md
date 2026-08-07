# The warehouse — architecture (SSOT)

The platform's analytical store: every stream of history the product
keeps and learns from.  Telemetry was its first family; safety events,
driver stats, and geofence definitions already live here too, and any
feature can add its own — the design is family-agnostic on purpose.
If code and this document disagree, one of them is wrong — fix
whichever, but never silently.

## Quick orientation (read this much before touching anything)

- **What it is**: tiered history in the Postgres `warehouse` schema.
  Grains: `live · minute · hour · day · week`.  Three data categories
  decide who gets grains: **assets** climb the full ladder, **logs**
  keep full fidelity + counts in the tiers, **caches** hold one
  current row (details below).
- **Where things live** (one kind of thing per layer):
  data → `warehouse.*` schema · SQL → `adapters/storage/warehouse/`
  · feature logic → `features/<x>/warehouse/` · declarations →
  `features/<x>/lifecycle.py` · engines →
  `capabilities/data_lifecycle/` (ACQUIRE · BUILD · KEEP).
- **How to read**: see “Reading from the warehouse”.
- **How to add a stream**: see “Adding a dataset — the recipe”.
- **The five iron rules**: reads via the grain names, WRITES only in
  the machinery (CI-guarded) · one vocabulary — tiered streams are
  named `<stream>_<grain>` at every layer · `source_ts` is truth and
  is never minted · sample labels sit on the time grid · capabilities
  never import features.

## Reading from the warehouse (for feature developers)

- **Address everything by its grain name** — `vehicle_state_hour`,
  `vehicle_health_day`, `vehicle_timeline`.  (Since migration 188/189
  most grain names ARE the physical tables; the health minute..week
  names are column-slice views.  You cannot tell the difference from
  SQL, which is the point.)  WRITES outside the machinery fail CI.  Per-grain: `warehouse.vehicle_state_live/minute/hour/
  day/week`, `warehouse.vehicle_health_*`.  Cross-grain:
  `warehouse.vehicle_timeline` (grain as a column).
- **Join identity on `registry_id`** (Postgres `vehicles.id`), never
  on names; names are display labels.
- **Trust freshness only via `source_ts`** with the shared
  `is_stale()` (`capabilities/data_lifecycle/staleness.py`); unknown
  age IS stale.  Readers fall back to the live provider on AGE, not
  on emptiness (`features/vehicles/warehouse/readers.py` is the
  worked example).
- In Python, prefer the read facade
  (`features/vehicles/warehouse/service.py`) or the storage methods
  over raw SQL.

## Adding a dataset — the recipe

Copy `features/events/lifecycle.py` (33 lines — the template) and:

1. **Pick the category** (asset / log / cache — table below).  It
   decides whether you declare rollup stages at all.
2. **Name tables by the template** `<domain-noun>_<subtask>[_<kind>]`
   — no grain, no role word, no `warehouse_` prefix; create them in
   the `warehouse` schema (migration).
3. **Declare in `features/<you>/lifecycle.py`**: an `IngestDataset`
   (key, owner, job_id, cadence, run, tables, freshness SLA,
   `expect_rows=False` for sparse feeds); rollup stages + `reroll`
   if an asset; retention targets + needs.
4. **Register the module**: one line in each hub's `_CONTRIBUTORS`
   roster (`capabilities/data_lifecycle/{ingest,rollups,retention}/
   __init__.py`).
5. Domain math (aggregation, readers) goes in
   `features/<you>/warehouse/` — never in the engines.

Everything else is free and automatic: the scheduler runs it, the
ledger records it, the watchdog guards it, the catalog stamps the
tables, `/telemetry/warehouse-status` reports it, retention prunes
it.  Zero engine edits — the engines never learn your name.

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
  drivers/lifecycle.py          # driver_efficiency_day dataset
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

One dataset = one STREAM name; resolution is a GRAIN.  For tiered
(asset) streams the physical tables ARE named by grain —
`<stream>_<grain>`: `vehicle_state_live / _minute / _hour / _day /
_week` — one vocabulary at every layer (migration 188).

**Recorded reversal (2026-08-06):** the arc's original rule was
"shape decides the table, grain is a label" (one aggregate table with
a `granularity` column).  It was RIGHT while hundreds of call sites
spoke physical names, and it was deliberately reversed once the
grain-surface views had moved every consumer onto the grain names:
at that point the label column bought nothing (writers, pruners and
builders were already per-grain) and cost a permanent second
vocabulary.  Owner asked three times; the advisor endorsed ("not
vanity").  Do not merge the tier tables back — and do not cite the
old rule against per-grain tables for a stream whose consumers
address grains.  The residual cost is known and accepted: a column
added to all three aggregate tiers is three ALTERs.
`vehicle_timeline` remains the cross-grain surface (grain as a
column, five branches).

**The `warehouse` schema (decided 2026-08-03, pre-customer window):**
the whole family — 13 tables + the view — lives in a dedicated
Postgres schema, moved by the operator window script
(docs/runbooks/warehouse-schema-move.md) and mirrored by idempotent
migration 183 for fresh installs.  Table names were then normalized
in stages (183 → 190; the History section has the chain) to today's
grain vocabulary; `warehouse_ingest_orphans → ingest_orphans` set the
rule that the schema IS the prefix — no table repeats the word.
`safety_event_log` keeps its name — "safety event" is the
industry's own noun here, not a role word.  Unqualified queries
resolve via the pool search_path `public,warehouse` (public FIRST so
unqualified CREATEs land in public; the shadow-orphan guard test in
tests/test_source_ts.py asserts no warehouse name ever reappears
there).  Explicit `warehouse.` qualification of the ~900 code
references is a later, CI-guarded follow-up — never part of cutover.

| Stream | Grain | Kind | Physical table | Kept |
|---|---|---|---|---|
| vehicle.timeline | live | sample | `vehicle_state_live` | until departed |
| vehicle.timeline | minute | sample | `vehicle_state_minute` | 7 d |
| vehicle.timeline | hour | aggregate | `vehicle_state_hour` | 90 d |
| vehicle.timeline | day | aggregate | `vehicle_state_day` | 730 d |
| vehicle.timeline | week | aggregate | `vehicle_state_week` | 1825 d |

**Asset grain addressing:** every asset stream is addressed as
`<stream>_<grain>`.  For `vehicle_state_*` these are the PHYSICAL
tables themselves (migration 188 completed the interface-first swap);
`vehicle_health_live` is its own table (a separate provider feed);
`vehicle_health_minute..week` are column-slice views over the state
tables:

| grain | state stream | health stream |
|---|---|---|
| live | `warehouse.vehicle_state_live` | `warehouse.vehicle_health_live` |
| minute | `warehouse.vehicle_state_minute` | `warehouse.vehicle_health_minute` |
| hour | `warehouse.vehicle_state_hour` | `warehouse.vehicle_health_hour` |
| day | `warehouse.vehicle_state_day` | `warehouse.vehicle_health_day` |
| week | `warehouse.vehicle_state_week` | `warehouse.vehicle_health_week` |

Reads address the grain names freely; WRITES stay machinery-only
(CI-guarded write-verb rule).  The interface-first bridge already
paid out once: the 185 views moved every consumer onto these names,
which made the 188 physical swap invisible.  `vehicle_timeline`
remains the cross-grain surface (all five grains, one query).

The minute grain samples at the live ingest's own 60-second cadence
(pre-2026-08 history is 5-minute spaced; duty math is gap-based and
cadence-independent, so both spacings compute correctly).  There is
NO `granularity` column anywhere — grain lives in the table name
(migration 188 retired the column with the unified table).  The only
place the legacy words `hourly/daily/weekly` survive is the
tier-freshness API labels, kept because the operator console keys on
them (wire).  Every NEW dataset adopts the grain vocabulary from day
one.

**Table-name template (Version B, 2026-08-07):** every table of
READINGS carries its true grain — tiered assets `<stream>_<grain>`
(`vehicle_state_hour`, `driver_efficiency_day`), current-row caches
`<stream>_live` (`vehicle_health_live`, `weather_live`).  Logs and
catalogs keep kind names (`safety_event_log`, `vehicle_fault_log`,
`geofence_definitions`) — grain vocabulary applies to readings, not
to events or reference data.  Never a role word (PERSONA.md), never a
`warehouse_` prefix (the schema is the prefix).  "snapshot" is a
RETIRED word — it predates the grain vocabulary and cannot say which
grain it means.  New tables are born in the `warehouse`
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
  `vehicle_fault_log`, `device_event_log`); never resampled, their
  COUNTS ride the aggregate tiers (`harsh_event_count`,
  `fault_count_eod`).  `device_event_log` is the identity watch: the
  ingest appends a row whenever an anchor behind a provider vehicle id
  moves — VIN (`vin_change`: different truck), gateway serial
  (`gateway_swap`: different hardware), or an implausible odometer jump
  (`odo_rebase`: different scale — truck 128's silent scale change
  became a 337,931-mile month).  Delivery is TENANT-side: the events
  are the account's fleet news, so the account's admins hear about
  them through the notifications capability (`alert.device_identity`,
  `capabilities/alerting/device_identity.py`) — never a
  platform-operator channel.
- **Cache** — re-fetchable convenience kept for speed
  (`vehicle_health_live`, `vehicle_fault_live`, `weather_live`,
  `efficiency_live`, `geofence_definitions`); one current row —
  which is exactly what the `_live` grain suffix says.

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
| vehicles.state / health / faults | vehicle_state_live/minute, vehicle_health_live, vehicle_fault_live, vehicle_fault_log (+ device_event_log, appended by state ingest's identity watch) | features/vehicles |
| vehicles.weather / efficiency | weather_live, efficiency_live | features/vehicles (account-wide *vehicle* aggregates — previously unowned) |
| events.safety | safety_event_log | features/events (docs/FEATURES.md "that's platform" line is superseded) |
| drivers.efficiency | driver_efficiency_day | features/drivers |
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
`account_id` (vehicle_state, vehicle_health_live,
vehicle_fault_live, weather_live, geofence_definitions —
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

## History (the 2026 refactor arc, condensed)

Born from a 65-finding audit (2026-07-31; owner + advisor approved
the target design).  Phase 1 rescued damaged data (step-based miles,
re-roll repair); Phase 2 fixed root causes (`source_ts`, ingest
registry + ledger); Phase 3 built identity (`registry_id`, security
walls, departure lifecycle); Phase 4 hardened (watchdog, cadences,
age-based reader fallback, the registry-driven status page); Phase 5
re-homed everything into the tree above and dissolved
`capabilities/warehouse/`.  Along the way, owner-driven: the
`warehouse` Postgres schema + four table renames (2026-08-03), the
minute grain replacing 5-minute sampling, the grain surfaces, the
gauge ladder, and the physics step-guard.  Every audit finding was
repaired or explicitly retired.  Finally (2026-08-06) the grain
names went physical — migration 188 renamed live/minute and split the
aggregate tiers, retiring `vehicle_telemetry` and the `granularity`
column.  Details live in git history and the runbooks
(`docs/runbooks/warehouse-*.md`).
