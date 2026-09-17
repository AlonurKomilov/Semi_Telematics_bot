"""Vehicle state — ONE write path for the live reading, whoever reports it.

``vehicle_state_live`` is keyed by the truck, so two providers describing
the same truck cannot coexist there the way two ELDs coexist in
``driver_hos_live``: they would take turns overwriting each other every
minute, and the row would say whichever ran last.  The resolver's own
docstring names that as the case where one has to win — this module is
where the winning happens, and it happens per READING rather than per
provider: the position from the device with the fresh GPS, the odometer
from the device wired to the ECM, each moving whole with its own clock.

Provider packages register a COLLECTOR — fetch and reshape, no writes —
and this module resolves who serves the capability, asks each, arbitrates
where two rows meet on the same registry identity, and writes once.  A
provider that is alone (every account today) gets its rows written
verbatim: there is nothing to arbitrate, and the golden test pins that
the rows are what the Samsara path wrote before this module existed.

Here rather than under ``features/`` because the callers already live at
this layer — the Samsara backfill, the scheduler job, the operator's
"ingest now" — and ``capabilities/integrations`` may not import
``features`` (the layer guard).  What the store learns from it:
``field_provenance``, a JSON map of reading group → provider, on the
live row and carried onto the minute grain, so a surface can say where
each number came from the way the roster's Source card does.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from adapters.telematics.catalog import PROVIDER_CATALOG, ProviderStatus
from adapters.telematics.protocol import Capability
from capabilities import source as reconciliation
from capabilities.data_lifecycle.staleness import freshest, sla_minutes
from capabilities.integrations.shared.resolver import resolve_all_providers_for
from capabilities.source.readings import NEWEST, Reading, pick_readings
from infra.services import get_tenant_db

logger = logging.getLogger(__name__)

ENTITY = "vehicle_state"
DATASET_KEY = "vehicles.state"

#: Reading group → (its value columns, its clock column).  A group is the
#: unit that moves between providers; the clock is what freshness and
#: ``source_ts`` are read from.  ``engine_state`` rides with the fix
#: because it is derived from the same instant's speed.
READING_GROUPS: dict[str, tuple[tuple[str, ...], str]] = {
    "location":     (("lat", "lon", "speed_mph", "heading", "address",
                      "engine_state"), "captured_at"),
    "odometer":     (("odometer_mi",), "odometer_time"),
    "engine_hours": (("engine_hours",), "engine_hours_time"),
    "fuel":         (("fuel_pct",), "fuel_time"),
    "def":          (("def_pct",), "def_time"),
}

GROUP_LABELS = {
    "location":     "Location & motion",
    "odometer":     "Odometer",
    "engine_hours": "Engine hours",
    "fuel":         "Fuel level",
    "def":          "DEF level",
}

APPLIES_WHEN = (
    "Only when one truck is reported by more than one connected telematics "
    "provider. Each reading moves whole — a position and its time come from "
    "one device; a reading past the dataset's tolerance yields to a fresh one "
    "whatever the order says."
)


def state_providers() -> tuple[str, ...]:
    """Catalog providers that can be connected AND report live state, in
    catalog order.  Read from the catalog so the answer is stable at
    import; filtered to AVAILABLE so a metadata-only entry (no client
    yet) is not offered as something to win."""
    return tuple(
        pid for pid, entry in PROVIDER_CATALOG.items()
        if Capability.VEHICLE_STATE in entry.capabilities
        and entry.status == ProviderStatus.AVAILABLE
    )


def source_labels() -> dict[str, str]:
    """The pick-list carries providers AND one rule; a panel rendering
    ``__newest__`` raw would read as a broken provider id."""
    return {
        **{pid: PROVIDER_CATALOG[pid].display_name for pid in state_providers()},
        NEWEST: "Whichever reported most recently",
    }


async def _refuse_pin(db: Any, account_id: int, entity_id: Any,
                      field: str, value: Any) -> None:
    """The hub's conflict UI resolves by writing a chosen value as
    ``manual``.  Nobody hand-enters a GPS fix; a pinned position would
    stop moving and every map would show it."""
    raise HTTPException(
        409,
        "Live readings cannot be pinned by hand. Choose which provider "
        "wins instead.",
    )


reconciliation.register_reconciled_entity(
    ENTITY,
    fields=tuple(READING_GROUPS),
    default_precedence={g: state_providers() for g in READING_GROUPS},
    field_labels=GROUP_LABELS,
    # Catalog order first, then the rule — an account that never opens
    # the panel behaves exactly as it does today.
    sources=state_providers() + (NEWEST,),
    apply_resolution=_refuse_pin,
)


async def config_payload(db: Any, account_id: int) -> dict:
    """The panel's block, with the rule labelled as what it is."""
    return {
        **(await reconciliation.precedence_options(db, account_id, ENTITY)),
        "source_labels": source_labels(),
        "applies_when": APPLIES_WHEN,
    }


# ── collectors: what a provider package contributes ─────────────────

#: ``after_write(written_rows, n)`` — the provider's own side effects
#: that must follow the write (Samsara: registry upsert from the fleet
#: payload, the condition sweep, identity-watch notices).
AfterWrite = Callable[[list[dict[str, Any]], int], Awaitable[None]]


@dataclass
class StateBatch:
    provider_id: str
    #: Warehouse-shaped rows, one per vehicle; ``vehicle_id`` is the
    #: provider's own external id.  A collector that can resolve
    #: ``registry_id`` itself sets it; the ingest fills the rest by
    #: telematics ref and quarantines what is still unmatched.
    rows: list[dict[str, Any]]
    after_write: AfterWrite | None = None


Collector = Callable[[int, Any], Awaitable["StateBatch | None"]]

_COLLECTORS: dict[str, Collector] = {}

#: Provider modules that register a collector at import.  Listed the
#: way the rollup and retention hubs list their contributors: the
#: scheduler process must not depend on which vendor package some other
#: import happened to load first.
_CONTRIBUTORS = (
    "capabilities.integrations.samsara.sync",
)


def register_state_collector(provider_id: str, collect: Collector) -> None:
    _COLLECTORS[provider_id] = collect


def _load_contributors() -> None:
    for mod in _CONTRIBUTORS:
        try:
            importlib.import_module(mod)
        except Exception:
            logger.exception("vehicle_state: contributor %s failed to import", mod)


# ── arbitration ─────────────────────────────────────────────────────

def _reading(row: dict[str, Any], group: str, provider_id: str) -> Reading:
    cols, clock = READING_GROUPS[group]
    values = {c: row.get(c) for c in cols}
    if group == "location":
        # (0, 0) is the Gulf of Guinea, and no truck is there: a gateway
        # with no fix reports zeros.  Speed and heading may still make
        # the reading present — a parked truck's 0 mph is a value.
        if not values.get("lat") and not values.get("lon"):
            values["lat"] = values["lon"] = None
    return Reading(source=provider_id, values=values, time=row.get(clock) or None)


def stamp_provenance(rows: list[dict[str, Any]], provider_id: str) -> None:
    """One provider: every present group is theirs.  Rows otherwise
    untouched — the single-provider write is the provider's own row."""
    for r in rows:
        r["field_provenance"] = {
            g for g in READING_GROUPS if _reading(r, g, provider_id).present
        }
        r["field_provenance"] = {g: provider_id for g in sorted(r["field_provenance"])}


def _merge_one(cands: list[tuple[str, dict[str, Any]]],
               precedence: dict[str, tuple[str, ...]], sla_min: float,
               identity_refs: frozenset[str]) -> dict[str, Any]:
    chosen = pick_readings(
        {g: [_reading(r, g, pid) for pid, r in cands] for g in READING_GROUPS},
        precedence=precedence, sla_min=sla_min,
    )
    by_pid = {pid: r for pid, r in cands}
    # The base row — ``vehicle_id`` (the live table's key), name, company,
    # fault counts, the driver — is the row the registry links to by
    # telematics ref (Contract 3: ``vehicle_id`` is that external id).
    # NOT the fix's winner: an owner switching "Location" to newest
    # would otherwise change the row's KEY and mint a second live row
    # beside the stale first.  A truck no ref anchors falls back to the
    # fix's winner until the namespaced-key amendment lands.
    anchored = [pid for pid, r in cands
                if str(r.get("vehicle_id") or "") in identity_refs]
    if anchored:
        base_pid = anchored[0]
    elif "location" in chosen:
        base_pid = chosen["location"].source
    else:
        base_pid = cands[0][0]
    out = dict(by_pid[base_pid])
    for g, reading in chosen.items():
        cols, clock = READING_GROUPS[g]
        src = by_pid[reading.source]
        for c in cols:
            out[c] = src.get(c)
        out[clock] = src.get(clock)
    clocks = [out.get(READING_GROUPS[g][1]) for g in chosen]
    out["source_ts"] = freshest(*clocks) or out.get("source_ts")
    out["field_provenance"] = {g: r.source for g, r in sorted(chosen.items())}
    return out


def merge_batches(batches: list[StateBatch],
                  precedence: dict[str, tuple[str, ...]],
                  sla_min: float,
                  identity_refs: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Rows that meet on one registry identity become one row; the rest
    pass through with their provenance stamped.  Rows without a
    registry link cannot be the same truck as far as anyone can prove,
    so they stay apart under their provider's own id."""
    by_key: dict[Any, list[tuple[str, dict[str, Any]]]] = {}
    for b in batches:
        for r in b.rows:
            rid = r.get("registry_id")
            key = ("registry", rid) if rid is not None else (b.provider_id, r.get("vehicle_id"))
            by_key.setdefault(key, []).append((b.provider_id, r))
    out: list[dict[str, Any]] = []
    for cands in by_key.values():
        if len(cands) == 1:
            pid, r = cands[0]
            stamp_provenance([r], pid)
            out.append(r)
        else:
            out.append(_merge_one(cands, precedence, sla_min, identity_refs))
    return out


# ── the tick ────────────────────────────────────────────────────────

async def ingest_vehicle_state(account_id: int) -> int:
    """Ask every connected provider that reports live state, arbitrate,
    write ``vehicle_state_live`` once.  Returns the rows persisted.

    Resolves rather than assumes: the dataset no longer pins Samsara,
    so an account whose live state comes from another device is not
    gated on the Samsara integration row — the same reason the HOS
    dataset resolves.  No provider, or none answering, is ``0`` and a
    row left alone, never a blanked table.
    """
    tenant = await get_tenant_db(account_id)
    if tenant is None:
        return 0
    _load_contributors()
    providers = await resolve_all_providers_for(account_id, Capability.VEHICLE_STATE)
    batches: list[StateBatch] = []
    for pid in providers:
        collect = _COLLECTORS.get(pid)
        if collect is None:
            logger.debug(
                "vehicle_state: %s offers the capability but registered no "
                "collector acct=%d", pid, account_id,
            )
            continue
        try:
            batch = await collect(account_id, tenant)
        except Exception:
            logger.exception("vehicle_state: %s collect failed acct=%d", pid, account_id)
            continue
        if batch is not None and batch.rows:
            batches.append(batch)
    if not batches:
        return 0

    # Registry identity first, so two providers' rows for one truck can
    # meet on it.  Unmatched rows keep their last known link (the upsert
    # COALESCEs) and are quarantined for the watchdog.
    orphans: list[dict[str, Any]] = []
    try:
        ref_to_id = await tenant.registry_ids_by_telematics_ref(account_id)
    except Exception:
        logger.exception(
            "registry-id resolution unavailable acct=%d — rows keep their "
            "last known registry link", account_id,
        )
        ref_to_id = {}
    try:
        archived_refs = await tenant.operator_archived_refs(account_id)
    except Exception:
        logger.exception(
            "archived-vehicle filter unavailable acct=%d — ingesting "
            "everything this tick", account_id,
        )
        archived_refs = set()
    if ref_to_id:
        for b in batches:
            for row in b.rows:
                if row.get("registry_id") is not None:
                    continue
                vid = str(row.get("vehicle_id") or "")
                rid = ref_to_id.get(vid)
                if rid is not None:
                    row["registry_id"] = rid
                else:
                    orphans.append({
                        "external_id": vid,
                        "name": row.get("vehicle_name") or "",
                        "company_code": row.get("company_code") or "",
                    })
        if orphans:
            try:
                await tenant.record_ingest_orphans(account_id, DATASET_KEY, orphans)
            except Exception:
                logger.exception("orphan quarantine write failed acct=%d", account_id)

    precedence = await reconciliation.get_precedence(tenant, account_id, ENTITY)
    rows = merge_batches(
        batches, precedence, sla_minutes(DATASET_KEY),
        identity_refs=frozenset(ref_to_id),
    )

    if archived_refs:
        before = len(rows)
        rows = [r for r in rows
                if str(r.get("vehicle_id") or "") not in archived_refs]
        dropped = before - len(rows)
        if dropped:
            logger.info(
                "ingest_vehicle_state acct=%d dropped_archived=%d refs=%s",
                account_id, dropped, sorted(archived_refs)[:20],
            )

    n = await tenant.upsert_vehicle_state(account_id, rows)
    logger.info(
        "ingest_vehicle_state acct=%d providers=%s persisted=%d "
        "with_registry_id=%d orphans=%d",
        account_id, ",".join(b.provider_id for b in batches), n,
        sum(1 for r in rows if r.get("registry_id") is not None),
        len(orphans),
    )
    for b in batches:
        if b.after_write is None:
            continue
        try:
            await b.after_write(rows, n)
        except Exception:
            logger.exception(
                "vehicle_state: %s after-write failed acct=%d", b.provider_id, account_id,
            )
    return n
