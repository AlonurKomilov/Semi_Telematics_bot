"""Datatruck sync engine — pulls TMS resources into ``datatruck_*``.

One ``sync_resource(account_id, resource)`` call walks a resource's
paginated list endpoint (rate-gated by the client at 18 req/min),
normalizes each raw record into the promoted columns, and upserts via
the storage mixins.  Idempotent end-to-end: re-running a sync updates
rows in place (UNIQUE(account_id, external_id)).

Progress is published to Redis (``datatruck_sync:{account}:{resource}``,
24h TTL) in the same heartbeat pattern as the Samsara backfill badge —
the dashboard polls the status endpoint and renders "syncing · page
4/6" → "60 drivers · synced 2 min ago".

## Field normalization

Datatruck's docs are thin on field-level shape, so every normalizer
is a defensive ``.get()`` chain over the names we've observed (live
probe of a real tenant, 2026-06-10: snake_case keys — ``unit_number``,
``plate_number``, ``vin``) plus camelCase fallbacks.  A field the
normalizer misses is NOT lost — the full raw record rides in the
``payload`` column, so improving a normalizer later back-fills from
local data on the next sync.

## Page caps

``orders`` on a mature tenant holds 17k+ records = 1,700+ pages at
the API's 10-item ceiling (~95 min of rate budget).  Each resource
declares a per-run page cap; capped runs surface ``pages_done`` and
``total_upstream`` in the status so the operator sees "synced 500 of
17,093" rather than a silent partial.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from adapters.telematics.protocol import Capability
from capabilities.integrations.shared.helpers import (
    _for_each_account_with_capability,
)
from infra import cache as _redis
from infra.platform import get_platform_db, get_tenant_db
from infra.services import get_telematics_client

logger = logging.getLogger(__name__)

_PROVIDER_ID = "datatruck"
_STATUS_TTL_SEC = 24 * 3600


# ── Normalization helpers ─────────────────────────────────────────


def _as_text(value: Any, *keys: str) -> str:
    """Coerce a possibly-nested API value to display text.

    Datatruck nests related entities (``{"driver": {"id": 8, "name":
    "..."}}``) in some responses and returns bare scalars in others.
    ``keys`` are the candidate fields tried in order when the value
    is a dict; scalars stringify directly.
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        for k in keys or ("name", "title", "display_name"):
            inner = value.get(k)
            if inner not in (None, ""):
                return str(inner)
        return ""
    return str(value)


def _as_id(value: Any) -> str:
    """Extract an upstream id from a scalar or nested entity."""
    if value is None:
        return ""
    if isinstance(value, dict):
        inner = value.get("id")
        return "" if inner is None else str(inner)
    return str(value)


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> float | None:
    """Coerce to a 2-decimal money value (Datatruck sends 4-decimal prices);
    keeps None when upstream omits the field."""
    f = _as_float(value)
    return round(f, 2) if f is not None else None


def _first(rec: dict, *keys: str) -> Any:
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""):
            return v
    return None


def _norm_driver(rec: dict[str, Any]) -> dict[str, Any]:
    # The drivers-list endpoint nests the person under ``account``
    # (account.first_name / last_name / full_name / email); top-level
    # keys stay as fallback probes for other response shapes.
    acct = rec.get("account")
    if not isinstance(acct, dict):
        acct = {}
    first = _as_text(_first(acct, "first_name", "firstName")
                     or _first(rec, "first_name", "firstName"))
    last = _as_text(_first(acct, "last_name", "lastName")
                    or _first(rec, "last_name", "lastName"))
    display = _as_text(_first(acct, "full_name", "fullName")
                       or _first(rec, "name", "full_name", "fullName"))
    if not display:
        display = f"{first} {last}".strip()
    return {
        "external_id":  _as_id(rec.get("id")),
        "first_name":   first,
        "last_name":    last,
        "display_name": display,
        "phone":        _as_text(_first(
            rec, "contact_number", "phone", "phone_number", "phoneNumber")),
        "email":        _as_text(_first(acct, "email")
                                 or _first(rec, "email", "email_address")),
        "status":       _as_text(_first(rec, "status", "state"), "name"),
        "payload":      rec,
    }


def _norm_truck(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id":   _as_id(rec.get("id")),
        "unit_number":   _as_text(_first(rec, "unit_number", "unitNumber", "number")),
        "plate_number":  _as_text(_first(rec, "plate_number", "plateNumber", "plate")),
        "vin":           _as_text(rec.get("vin")),
        "make":          _as_text(rec.get("make"), "name"),
        "model":         _as_text(rec.get("model"), "name"),
        "year":          _as_int(rec.get("year")),
        "status":        _as_text(_first(rec, "status", "state"), "name"),
        "owner_name":    _as_text(_first(rec, "owner_name", "owner", "ownerName"), "name"),
        "operator_name": _as_text(_first(rec, "operator", "operator_name", "driver"), "name"),
        "odometer":      _as_float(_first(rec, "odometer", "mileage")),
        "payload":       rec,
    }


def _norm_trailer(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id":  _as_id(rec.get("id")),
        "unit_number":  _as_text(_first(rec, "unit_number", "unitNumber", "number")),
        "plate_number": _as_text(_first(rec, "plate_number", "plateNumber", "plate")),
        "vin":          _as_text(rec.get("vin")),
        "make":         _as_text(rec.get("make"), "name"),
        "model":        _as_text(rec.get("model"), "name"),
        "year":         _as_int(rec.get("year")),
        "status":       _as_text(_first(rec, "status", "state"), "name"),
        "payload":      rec,
    }


def _stop_place(stops: Any, stop_type: str, *, last: bool = False) -> str:
    """"City, ST" for the first pickup / last delivery stop.  The openapi
    order carries a ``stops`` array with nested ``location`` objects."""
    if not isinstance(stops, list):
        return ""
    cands = [
        s for s in stops
        if isinstance(s, dict)
        and str(s.get("stop_type") or "").strip().lower() == stop_type
    ]
    if not cands:
        return ""
    s = cands[-1] if last else cands[0]
    loc = s.get("location")
    if not isinstance(loc, dict):
        return _as_text(loc)
    city = str(loc.get("city") or "").strip()
    state = str(loc.get("state") or "").strip()
    if city or state:
        return ", ".join(p for p in (city, state) if p)
    return _as_text(loc, "place_name", "company", "address1")


def _norm_order(rec: dict[str, Any]) -> dict[str, Any]:
    # Field names verified against the openapi docs (get_loads_list /
    # get_load, 2026-07-03): the API uses Django-style compound keys
    # (``customer__company_name``) plus a nested ``trip`` object; there are
    # NO numeric driver/truck ids — only names / unit numbers.  The older
    # generic probes stay as fallbacks for shape drift.
    trip = rec.get("trip") if isinstance(rec.get("trip"), dict) else {}
    adt = rec.get("assigned_driver_n_truck") \
        if isinstance(rec.get("assigned_driver_n_truck"), dict) else {}
    stops = rec.get("stops")
    total_miles = _as_float(rec.get("total_miles"))
    loaded = _as_float(_first(
        {**rec, "trip_mile": trip.get("mile")},
        "trip_mile", "loaded_miles", "loadedMiles", "miles_loaded",
    ))
    empty = _as_float(_first(
        {**rec, "trip_empty": trip.get("empty_mile")},
        "trip_empty", "empty_miles", "emptyMiles", "deadhead_miles",
    ))
    if loaded is None and total_miles is not None:
        # Only the order-level total is known — derive loaded from it so
        # RPM / totals stay right (empty defaults to 0 in that case).
        loaded = round(total_miles - (empty or 0.0), 1)
    return {
        "external_id":        _as_id(rec.get("id")),
        # shipment_id ("DT-002489") is the human-facing number Datatruck's
        # own list leads with; load_id ("T-111…") is the broker/customer
        # reference.  The raw pk stays the very last resort.
        # load_id ("958511703") is the broker/BOL reference the dispatcher
        # works from — the operator's chosen Load # — with shipment_id
        # ("DT-002489") as fallback and the raw pk as last resort.  The
        # per-account sequential display ID lives on our side (loads.seq).
        "order_number":       _as_text(_first(
            rec, "load_id", "shipment_id",
            "order_number", "orderNumber", "number", "load_number",
        )) or _as_id(rec.get("id")),
        "status":             _as_text(_first(rec, "status", "state"), "name"),
        # Dates trimmed to YYYY-MM-DD — the API sends full datetimes; our
        # loads model + date inputs use day precision.
        "pickup_date":        _as_text(_first(
            rec, "pickup_time", "pickup_appointment_time",
            "pickup_date", "pickupDate", "pickup_at", "start_date",
        ))[:10],
        "delivery_date":      _as_text(_first(
            rec, "delivery_time", "delivery_appointment_time",
            "delivery_date", "deliveryDate", "delivered_at", "end_date",
        ))[:10],
        "origin":             _stop_place(stops, "pickup") or _as_text(
            _first(rec, "origin", "pickup_location", "from_location"),
            "name", "city", "address",
        ),
        "destination":        _stop_place(stops, "delivery", last=True) or _as_text(
            _first(rec, "destination", "delivery_location", "to_location"),
            "name", "city", "address",
        ),
        "driver_external_id": _as_id(_first(rec, "driver", "driver_id")),
        "driver_name":        _as_text(_first(
            {**rec,
             "trip_driver": trip.get("driver__full_name"),
             "adt_driver": adt.get("driver_full_name")},
            "trip_driver", "adt_driver", "driver_name",
        )),
        "truck_external_id":  _as_id(_first(rec, "truck", "truck_id")),
        "truck_unit":         _as_text(_first(
            {**rec,
             "trip_truck": trip.get("truck__unit_number"),
             "adt_truck": adt.get("truck_unit_number")},
            "trip_truck", "adt_truck",
        )),
        "trailer_external_id": _as_id(_first(rec, "trailer", "trailer_id")),
        "trailer_unit":       _as_text(_first(
            {**rec,
             "trip_trailer": trip.get("trailer__unit_number"),
             "adt_trailer": adt.get("trailer_unit_number")},
            "trip_trailer", "adt_trailer",
        )),
        # total_pay = linehaul + accessorials — the revenue figure the
        # owner's KPI expects; load_pay alone is the linehaul.
        "total_rate":         _money(_first(
            rec, "total_pay", "load_pay",
            "total_rate", "totalRate", "rate", "total",
        )),
        "customer":           _as_text(_first(
            rec, "customer__company_name",
            "customer", "broker", "client", "customer_name",
        ), "name", "company_name"),
        "company_name":       _as_text(rec.get("mc_number__company_name")),
        "dispatcher_external_id": _as_id(_first(rec, "dispatcher", "dispatcher_id")),
        "dispatcher_name":    _as_text(_first(
            rec, "dispatcher__full_name",
            "dispatcher", "dispatcher_name",
        ), "name", "full_name", "username"),
        "loaded_miles":       loaded,
        "empty_miles":        empty,
        # DELIBERATELY NOT mapped from trip.total_load_pay: per the docs +
        # a live load, that field is the trip's share of the LOAD PAY
        # (revenue) — Datatruck's "Driver gross" display — not the driver's
        # earnings.  Actual driver earnings live in settlements, which this
        # endpoint doesn't expose; the generic keys stay for shape drift.
        "driver_pay":         _money(_first(
            rec, "driver_earnings", "driver_pay", "driverPay",
        )),
        # The trip's revenue share ("Driver gross" in Datatruck's UI) —
        # NOT driver earnings, but the BASIS a percentage tariff applies
        # to.  Promoted only for the opt-in pay estimate.
        "driver_gross_basis": _money(trip.get("total_load_pay")),
        # Accessorial lump: total_other_pay = every "additional payment"
        # on the order (layover/detention/TONU fees the customer was
        # billed) with no per-type breakdown — total_pay above already
        # includes it, this is the visible split.
        "other_pay":          _money(_first(
            rec, "total_other_pay", "other_pay",
        )),
        # Which settlement the load landed on (trip-nested compound keys).
        # Metadata only — the settlements themselves have no API.
        "settlement_ref":     _as_text(_first(
            {**rec, "trip_stl": trip.get("settlement__settlement_number")},
            "trip_stl", "settlement_number",
        )),
        "settlement_status":  _as_text(_first(
            {**rec, "trip_stl_status": trip.get("settlement__status")},
            "trip_stl_status", "settlement_status",
        ), "name"),
        "payload":            rec,
    }


def _norm_task(t: dict[str, Any]) -> dict[str, Any]:
    """Normalize one work-order line item (Datatruck ``work_order_tasks``).

    ``task`` references a catalog task; ``custom_task`` is a free-text
    one-off (the UI's "Task Name").  ``parts_price`` is per-unit, qty is
    ``parts_quantity``, and ``total_price`` is the line total (parts +
    labor).  Labor-only lines carry ``labor_price``/``labor_hours`` with
    a zero parts price — kept so the row still shows on the WO.
    """
    return {
        "name":        _as_text(
            _first(t, "custom_task", "task", "name"), "name", "title",
        ),
        "quantity":    _as_float(_first(t, "parts_quantity", "quantity", "qty")) or 0.0,
        # Datatruck sends 4-decimal prices; money rounds to cents.
        "unit_cost":   _money(_first(t, "parts_price", "unit_cost", "price")) or 0.0,
        "labor_price": _money(_first(t, "labor_price")) or 0.0,
        "labor_hours": _as_float(_first(t, "labor_hours")) or 0.0,
        "total":       _money(_first(t, "total_price", "total")) or 0.0,
    }


def _norm_work_order(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Datatruck work order.

    Field names match the v2 detail serializer (live-verified
    2026-06-16) with defensive fallbacks for the openapi list shape.
    Note the WO **number** ("WO-0001") and the **invoice id** ("INV-1001")
    are distinct fields — the module's ``invoice_number`` maps from
    ``invoice_id``, never the WO number.
    """
    tasks_raw = (
        rec.get("work_order_tasks")
        or rec.get("line_items")
        or rec.get("tasks")
        or []
    )
    line_items = [_norm_task(t) for t in tasks_raw if isinstance(t, dict)]
    # The WO carries EITHER a ``truck`` or a ``trailer`` (the other is None);
    # in the v2 detail each is a dict ({unit_number, plate_number, …}), in the
    # list shape it can be a bare string.  Capture which kind it is so the WO
    # links to the right registry vehicle (a truck "103" and a trailer "103"
    # are different assets), and pull the unit_number from the dict — falling
    # back to the string only when that's all upstream gives us.
    _truck = rec.get("truck")
    _trailer = rec.get("trailer")
    _vehicle = _truck or _trailer or _first(rec, "vehicle", "unit")
    _vendor = rec.get("vendor")
    return {
        "external_id":  _as_id(rec.get("id")),
        "number":       _as_text(_first(
            rec, "number", "work_order_number", "workOrderNumber",
        )) or _as_id(rec.get("id")),
        "status":       _as_text(_first(rec, "status", "state"), "name"),
        "vehicle_unit": _as_text(_vehicle, "unit_number", "number", "name"),
        "vehicle_type": "truck" if _truck else ("trailer" if _trailer else ""),
        "opened_at":    _as_text(_first(
            rec, "scheduled_on_date", "opened_at", "created_at",
            "createdAt", "date",
        )),
        "closed_at":    _as_text(_first(
            rec, "closed_at", "completed_at", "completedAt", "due_date",
        )),
        "total_cost":   _money(_first(
            rec, "total_price", "total_cost", "totalCost", "total", "cost",
        )),
        # ── Enrichment promoted onto the Work Orders module ──
        "invoice_number": _as_text(_first(
            rec, "invoice_id", "invoice_number", "invoiceId",
        )),
        "vendor_name":    _as_text(
            _first(rec, "vendor", "vendor_name", "vendorName"),
            "name", "company_name",
        ),
        "vendor_address": _as_text(_first(
            rec, "location", "vendor_location", "address",
        )),
        # Only a vendor DICT carries a phone; when ``vendor`` is a bare name
        # string, look for a top-level phone instead of echoing the name.
        "vendor_phone":   (
            _as_text(_vendor, "contact_number", "phone", "phone_number")
            if isinstance(_vendor, dict)
            else _as_text(_first(rec, "vendor_phone", "contact_number", "phone"))
        ),
        "payment_method": _as_text(_first(
            rec, "payment_type", "payment_method", "paymentType",
        )),
        "note":           _as_text(_first(rec, "note")),
        "tax_amount":     _money(_first(
            rec, "tax_total_price", "tax_amount", "tax",
        )),
        "odometer":       _as_float(rec.get("odometer")),
        # Carrier MC number — the openapi list gives the bare number string;
        # used to match the WO to one of the account's Companies (which own
        # the MC/DOT) so the page shows the real company.
        "mc_number":      _as_text(rec.get("mc_number"), "number", "company_name"),
        # Who the WO is assigned to (openapi gives a name string; detail a
        # nested user object — handle both).
        "assigned_to":    _as_text(
            rec.get("assigned_to"), "full_name", "name", "username",
        ),
        # Payment state (openapi-available): used to SEED payment_status
        # since payment_type/invoice_id live only in the v2 detail view.
        "balance":        _money(_first(rec, "balance", "balance_due")),
        "paid_amount":    _money(rec.get("paid_amount")),
        "line_items":     line_items,
        "payload":        rec,
    }


# ── Resource registry ─────────────────────────────────────────────


def _work_order_params(days: int | None = None) -> dict[str, str]:
    """Rolling window for the work-orders endpoint, which REQUIRES
    ``from_date`` / ``to_date``.  Defaults to 90 days back (a quarter
    of shop history) when the operator hasn't chosen a window; 7
    forward catches pre-scheduled work."""
    today = datetime.now(timezone.utc).date()
    back = days or 90
    return {
        "from_date": (today - timedelta(days=back)).isoformat(),
        "to_date":   (today + timedelta(days=7)).isoformat(),
    }


def _order_params(days: int | None = None) -> dict[str, str] | None:
    """Optional recency window for the orders endpoint.

    Orders has no *required* date range — it paginates newest-first
    under a 50-page cap — so the default (``days=None``) sends no
    filter and behaves exactly as before.  When the operator picks a
    window we pass ``from_date``/``to_date`` (same keys the work-orders
    endpoint proves the API honours) so a mature tenant can pull just
    recent loads instead of spending the whole page cap on old history.
    """
    if not days:
        return None
    today = datetime.now(timezone.utc).date()
    return {
        "from_date": (today - timedelta(days=days)).isoformat(),
        "to_date":   (today + timedelta(days=7)).isoformat(),
    }


@dataclass(frozen=True)
class ResourceSpec:
    """Everything the engine needs to sync one resource."""

    name: str
    path: str
    capability: str
    normalize: Callable[[dict], dict]
    upsert_method: str
    stats_method: str
    max_pages: int
    # Builds the query params for a run, given the operator-chosen
    # history window (days, or None for "no window / provider default").
    # None here means the resource takes no params at all (roster lists).
    params_factory: Callable[[int | None], dict | None] | None = None
    # When set ('truck' | 'trailer'), each synced page is ALSO projected
    # into the Vehicle registry (the SSOT) as this type.  None for non-
    # vehicle resources (drivers / orders / work_orders).
    registry_vehicle_type: str | None = None
    # When True, each synced page is ALSO projected into the module
    # ``work_orders`` table (the SSOT) so synced shop invoices land on
    # the Work Orders page beside hand-entered ones.
    project_work_orders: bool = False
    # When True, each synced page is ALSO linked onto the account's
    # existing driver-users (match by CDL → email; enrich-only, never
    # creates users — the one-time onboarding import does creation).
    project_drivers: bool = False
    # When True, each synced page is ALSO projected into the canonical
    # ``loads`` table (the Loads feature SSOT) — status mapped, driver
    # resolved via datatruck_driver_id, units via the synced rosters.
    project_loads: bool = False
    # When set, each list record is RE-FETCHED from this per-id endpoint
    # before normalizing, because the openapi list shape is leaner than
    # the detail view (work orders: the list omits invoice id, payment
    # type, vendor contact, location, and the unit number — all present
    # in detail).  ``{id}`` is substituted with the record's ``id``.
    detail_path_tmpl: str | None = None
    # The product FEATURE this resource lands in (display grouping on the
    # card's "Synced data" table).  Mirrors where the projector writes:
    # trucks/trailers → Vehicles registry, work_orders → Work Orders, etc.
    feature: str = ""


RESOURCES: dict[str, ResourceSpec] = {
    spec.name: spec for spec in (
        ResourceSpec(
            name="drivers", path="drivers/list/",
            capability=Capability.TMS_DRIVERS_SYNC,
            normalize=_norm_driver,
            upsert_method="upsert_datatruck_drivers",
            stats_method="datatruck_drivers_stats",
            max_pages=30,
            project_drivers=True,
            feature="Drivers",
        ),
        ResourceSpec(
            name="trucks", path="trucks/list/",
            capability=Capability.TMS_TRUCKS_SYNC,
            normalize=_norm_truck,
            upsert_method="upsert_datatruck_trucks",
            stats_method="datatruck_trucks_stats",
            max_pages=30,
            registry_vehicle_type="truck",
            feature="Vehicles",
        ),
        ResourceSpec(
            name="trailers", path="trailers/list/",
            capability=Capability.TMS_TRAILERS_SYNC,
            normalize=_norm_trailer,
            upsert_method="upsert_datatruck_trailers",
            stats_method="datatruck_trailers_stats",
            max_pages=30,
            registry_vehicle_type="trailer",
            feature="Vehicles",
        ),
        ResourceSpec(
            name="orders", path="orders/",
            capability=Capability.TMS_ORDERS_SYNC,
            normalize=_norm_order,
            upsert_method="upsert_datatruck_orders",
            stats_method="datatruck_orders_stats",
            # 50 pages × 10 records ≈ 3 min of rate budget per run.
            # A mature tenant's full order book (17k+) never fits one
            # run by design — the status shows synced-vs-upstream so
            # the cap is visible, and repeated runs converge if the
            # API returns newest-first.  A chosen history window narrows
            # the pull to recent loads so the cap goes further.
            max_pages=50,
            params_factory=_order_params,
            project_loads=True,
            feature="Loads / Orders",
        ),
        ResourceSpec(
            name="work_orders", path="work-orders/",
            capability=Capability.TMS_WORK_ORDERS_SYNC,
            normalize=_norm_work_order,
            upsert_method="upsert_datatruck_work_orders",
            stats_method="datatruck_work_orders_stats",
            max_pages=30,
            params_factory=_work_order_params,
            project_work_orders=True,
            feature="Work Orders",
            # NOTE: the richer v2 detail view (invoice id, payment type,
            # location, vendor contact, unit number) is NOT reachable with
            # an openapi token — it returns 401 (verified 2026-06-16:
            # openapi tokens are scoped to /api/v1/openapi/ only, and the
            # non-openapi v1 + all v2 routes reject them).  So detail-fetch
            # stays OFF; we enrich purely from the openapi list.  To
            # re-enable if a v2-scoped token is ever configured, set:
            #   detail_path_tmpl="api/v2/truck/truck/work_order/{id}/detail/"
        ),
    )
}


# ── Redis status ──────────────────────────────────────────────────


def _status_key(account_id: int, resource: str) -> str:
    return f"datatruck_sync:{account_id}:{resource}"


async def _publish(account_id: int, resource: str, payload: dict) -> None:
    try:
        if not _redis.is_available():
            return
        await _redis.cache_set(
            _status_key(account_id, resource),
            {**payload, "last_heartbeat": time.time()},
            ttl=_STATUS_TTL_SEC,
        )
    except Exception as e:
        logger.debug("datatruck sync status publish failed: %s", e)


async def get_sync_status(account_id: int, resource: str) -> dict | None:
    """Latest Redis record for one resource, or None.  Same stale-
    heartbeat coercion as the backfill badge: a record stuck at
    ``running`` for >5 min without a heartbeat is reported as failed
    so a crashed task can't wedge the 409 preflight forever."""
    try:
        if not _redis.is_available():
            return None
        payload = await _redis.get(_status_key(account_id, resource))
        if not isinstance(payload, dict):
            return payload
        if payload.get("state") == "running":
            hb = payload.get("last_heartbeat") or 0
            try:
                stale = (time.time() - float(hb)) > 300
            except (TypeError, ValueError):
                stale = True
            if stale:
                return {
                    **payload,
                    "state": "failed",
                    "error": "stale heartbeat — task died mid-flight",
                }
        return payload
    except Exception as e:
        logger.debug("datatruck sync status read failed: %s", e)
        return None


# ── Engine ────────────────────────────────────────────────────────


async def _enrich_with_detail(
    client: Any, spec: "ResourceSpec", records: list[Any], status: dict,
) -> list[dict]:
    """Replace each list record with its richer per-id detail body.

    Best-effort per record: a missing id or a failed/non-2xx detail
    fetch falls back to the original list record so the row is never
    dropped — it just keeps the leaner fields.

    Observability: counts successes/failures into ``status`` and records
    the first failure's HTTP code, so a run where the token can't reach
    the detail endpoint is VISIBLE (``detail_failed`` > 0) instead of
    silently blanking the rich fields.  A 401/403 here means the
    integration token isn't scoped for the detail endpoint.
    """
    out: list[dict] = []
    for r in records:
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        if rid in (None, ""):
            out.append(r)
            continue
        try:
            http, body = await client.get_path(
                spec.detail_path_tmpl.format(id=rid),
            )
            if http < 400 and isinstance(body, dict):
                out.append(body)
                status["detail_ok"] = status.get("detail_ok", 0) + 1
            else:
                out.append(r)
                status["detail_failed"] = status.get("detail_failed", 0) + 1
                if not status.get("detail_error"):
                    status["detail_error"] = (
                        f"HTTP {http} from detail endpoint — the integration "
                        "token may not be scoped for it"
                    )
                    logger.warning(
                        "datatruck %s detail fetch HTTP %s for id=%s — "
                        "token likely lacks detail scope; falling back to "
                        "the lean list record",
                        spec.name, http, rid,
                    )
        except Exception as e:
            out.append(r)
            status["detail_failed"] = status.get("detail_failed", 0) + 1
            if not status.get("detail_error"):
                status["detail_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            logger.debug(
                "datatruck %s detail fetch failed id=%s: %s",
                spec.name, rid, e,
            )
    return out


async def _fetch_vehicle_lookup(account_id: int) -> dict[str, list[str]]:
    """Plate/unit (lower) → ``[unit, type]`` map from the LIVE trucks +
    trailers rosters, so a work-order's vehicle reference resolves to the
    canonical unit number WITHOUT requiring (or writing to) a separate
    trucks/trailers sync.  Read-only; nothing is stored.  Best-effort —
    a roster fetch failure just yields a smaller map (the WO keeps its
    raw plate).  Values are lists (not tuples) so the map round-trips
    cleanly through the JSON preview cache."""
    lookup: dict[str, list[str]] = {}
    try:
        provider = await get_telematics_client(account_id, _PROVIDER_ID)
        client = provider.client  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("vehicle lookup: provider unavailable: %s", e)
        return lookup
    for res, vtype in (("trucks", "truck"), ("trailers", "trailer")):
        spec = RESOURCES[res]
        try:
            async for page in client.iter_pages(
                spec.path, None, max_pages=spec.max_pages,
            ):
                for raw in (page.get("results") or []):
                    if not isinstance(raw, dict):
                        continue
                    n = spec.normalize(raw)
                    unit = str(n.get("unit_number") or "").strip()
                    if not unit:
                        continue
                    for key in (unit, str(n.get("plate_number") or "").strip()):
                        if key:
                            lookup.setdefault(key.lower(), [unit, vtype])
        except Exception as e:
            logger.debug("vehicle lookup fetch %s failed: %s", res, e)
    return lookup


async def _persist_rows(
    account_id: int, spec: "ResourceSpec", normalized: list[dict], tenant: Any,
    *, vehicle_lookup: dict | None = None,
) -> int:
    """Write one batch of normalized rows: upsert into the datatruck_*
    snapshot, then project into the SSOT (vehicle registry / work-orders
    module).  Projections are best-effort so a hiccup never fails the
    sync.  ``vehicle_lookup`` (when given) lets the work-order projection
    resolve plate→unit without a trucks/trailers sync.  Shared by the
    streaming sync and the preview-apply path so both persist identically."""
    written = await getattr(tenant, spec.upsert_method)(account_id, normalized)
    if spec.registry_vehicle_type:
        try:
            await tenant.project_external_vehicles(
                account_id, normalized,
                vehicle_type=spec.registry_vehicle_type, source="datatruck",
            )
        except Exception as e:
            logger.debug(
                "datatruck registry projection skipped acct=%d: %s",
                account_id, e,
            )
    if spec.project_work_orders:
        try:
            await tenant.project_external_work_orders(
                account_id, normalized, source="datatruck",
                vehicle_lookup=vehicle_lookup,
            )
        except Exception as e:
            logger.debug(
                "datatruck work-order projection skipped acct=%d: %s",
                account_id, e,
            )
    if spec.project_drivers:
        try:
            await tenant.project_datatruck_drivers(account_id, normalized)
        except Exception as e:
            logger.debug(
                "datatruck driver projection skipped acct=%d: %s",
                account_id, e,
            )
    if spec.project_loads:
        try:
            await tenant.project_external_loads(
                account_id, normalized, source="datatruck",
            )
        except Exception as e:
            logger.debug(
                "datatruck loads projection skipped acct=%d: %s",
                account_id, e,
            )
    return written


async def fetch_normalized(
    account_id: int, resource: str, *, days: int | None = None,
) -> tuple[list[dict], int | None]:
    """Fetch + normalize every page of a resource WITHOUT writing.

    Backs the preview path: we pull the data, hand it to a planner to
    compute the diff, and cache it so apply commits the very same rows.
    Returns ``(rows, total_upstream)``.
    """
    spec = RESOURCES.get(resource)
    if spec is None:
        raise ValueError(f"unknown datatruck resource {resource!r}")
    provider = await get_telematics_client(account_id, _PROVIDER_ID)
    client = provider.client  # type: ignore[attr-defined]
    params = spec.params_factory(days) if spec.params_factory else None
    rows: list[dict] = []
    total: int | None = None
    async for page in client.iter_pages(
        spec.path, params, max_pages=spec.max_pages,
    ):
        if total is None:
            total = page.get("count")
        records = page.get("results") or []
        rows.extend(spec.normalize(r) for r in records if isinstance(r, dict))
    return rows, total


# ── Preview (plan → apply) ─────────────────────────────────────────
#
# Resources whose sync MERGES into a user-owned SSOT (the vehicle
# registry, the work-orders module) support a dry-run: preview fetches +
# diffs + caches the rows, the operator approves, then apply commits the
# CACHED rows (no re-fetch, no staleness).  Roster-only resources
# (drivers) and the huge append-only orders feed skip it.
_PREVIEWABLE = ("trucks", "trailers", "work_orders")
_PREVIEW_TTL_SEC = 600


def _preview_key(account_id: int, resource: str, preview_id: str) -> str:
    return f"datatruck_preview:{account_id}:{resource}:{preview_id}"


async def _plan_diff(
    tenant: Any, account_id: int, spec: "ResourceSpec", rows: list[dict],
    *, vehicle_lookup: dict | None = None,
) -> dict:
    """Dispatch to the right read-only planner for the resource."""
    if spec.registry_vehicle_type:
        return await tenant.plan_external_vehicles(
            account_id, rows, vehicle_type=spec.registry_vehicle_type,
        )
    if spec.project_work_orders:
        return await tenant.plan_external_work_orders(
            account_id, rows, source=_PROVIDER_ID, vehicle_lookup=vehicle_lookup,
        )
    return {"kind": "none", "counts": {"total": len(rows)}}


async def _cache_preview(key: str, payload: dict) -> None:
    if _redis.is_available():
        try:
            await _redis.cache_set(key, payload, ttl=_PREVIEW_TTL_SEC)
        except Exception as e:
            logger.debug("datatruck preview cache write failed: %s", e)


async def run_preview(
    account_id: int, resource: str, preview_id: str, *,
    days: int | None = None, triggered_by: int = 0,
) -> dict[str, Any]:
    """Background worker: fetch + diff a previewable resource WITHOUT
    writing, then cache the result + rows under ``preview_id``.

    Runs in the background (the upstream fetch is rate-gated and can
    outlast the 30s HTTP timeout), publishing ``running`` → ``ready`` /
    ``failed`` to Redis so the dashboard polls for the diff.  The cached
    ``rows`` are what ``apply_resource`` later commits.
    """
    key = _preview_key(account_id, resource, preview_id)
    await _cache_preview(key, {
        "state": "running", "resource": resource, "preview_id": preview_id,
        "fetched": 0, "total_upstream": None, "diff": None, "error": None,
    })
    try:
        if resource not in _PREVIEWABLE:
            raise ValueError(f"{resource!r} does not support preview")
        spec = RESOURCES[resource]
        tenant = await get_tenant_db(account_id)
        if tenant is None:
            raise RuntimeError("tenant DB unavailable")
        rows, total = await fetch_normalized(account_id, resource, days=days)
        # Work orders reference the vehicle by plate/unit string; pull the
        # rosters live (read-only) so the diff + apply show the canonical
        # unit number, with no separate trucks/trailers sync needed.
        vehicle_lookup = (
            await _fetch_vehicle_lookup(account_id)
            if spec.project_work_orders else None
        )
        diff = await _plan_diff(
            tenant, account_id, spec, rows, vehicle_lookup=vehicle_lookup,
        )
        payload = {
            "state": "ready", "resource": resource, "preview_id": preview_id,
            "fetched": len(rows), "total_upstream": total, "diff": diff,
            "error": None, "rows": rows, "days": days,
            "vehicle_lookup": vehicle_lookup,
        }
    except Exception as e:
        logger.exception(
            "datatruck preview failed acct=%d resource=%s", account_id, resource,
        )
        payload = {
            "state": "failed", "resource": resource, "preview_id": preview_id,
            "fetched": 0, "total_upstream": None, "diff": None,
            "error": str(e)[:300],
        }
    await _cache_preview(key, payload)
    return payload


async def get_preview(
    account_id: int, resource: str, preview_id: str,
) -> dict[str, Any]:
    """Poll target: the cached preview MINUS the bulky ``rows`` (kept
    server-side for apply).  ``pending`` until the worker writes its
    first state; ``expired`` once the cache TTL lapses."""
    if not _redis.is_available():
        return {"state": "unavailable", "preview_id": preview_id}
    cached = await _redis.get(_preview_key(account_id, resource, preview_id))
    if not isinstance(cached, dict):
        return {"state": "pending", "preview_id": preview_id}
    # Strip the server-side-only bulk (the rows to apply, the roster map).
    return {k: v for k, v in cached.items()
            if k not in ("rows", "vehicle_lookup")}


async def apply_resource(
    account_id: int, resource: str, preview_id: str, *, triggered_by: int = 0,
) -> dict[str, Any]:
    """Commit a previously-previewed resource from its cached rows.

    Reuses the sync status/heartbeat shape so the dashboard panel shows
    the same queued→running→completed progression.  Never raises — a
    missing/expired preview lands as ``state='failed'``.
    """
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status: dict[str, Any] = {
        "state": "running", "resource": resource, "account_id": account_id,
        "pages_done": 0, "records_written": 0, "total_upstream": None,
        "started_at": started, "finished_at": None, "error": None,
        "triggered_by": triggered_by, "applied_from_preview": preview_id,
    }
    await _publish(account_id, resource, status)
    try:
        spec = RESOURCES.get(resource)
        if spec is None:
            raise ValueError(f"unknown datatruck resource {resource!r}")
        cached = None
        if _redis.is_available():
            cached = await _redis.get(_preview_key(account_id, resource, preview_id))
        if not isinstance(cached, dict) or "rows" not in cached:
            raise RuntimeError(
                "preview expired or not found — re-run the preview and "
                "apply again within 10 minutes",
            )
        tenant = await get_tenant_db(account_id)
        if tenant is None:
            raise RuntimeError("tenant DB unavailable")
        rows = cached["rows"] or []
        status["total_upstream"] = cached.get("total_upstream")
        # Reuse the roster map the preview captured, so apply resolves the
        # same plate→unit the operator saw (no re-fetch, no staleness).
        status["records_written"] = await _persist_rows(
            account_id, spec, rows, tenant,
            vehicle_lookup=cached.get("vehicle_lookup"),
        )
        status["pages_done"] = 1
        status["state"] = "completed"
        # One-shot: drop the cache so a stale preview can't be re-applied.
        if _redis.is_available():
            try:
                await _redis.delete(_preview_key(account_id, resource, preview_id))
            except Exception:
                pass
    except Exception as e:
        logger.exception(
            "datatruck apply failed acct=%d resource=%s", account_id, resource,
        )
        status["state"] = "failed"
        status["error"] = str(e)[:300]
    finally:
        status["finished_at"] = datetime.now(
            timezone.utc,
        ).isoformat(timespec="seconds")
        await _publish(account_id, resource, status)
    return status


async def sync_resource(
    account_id: int,
    resource: str,
    *,
    triggered_by: int = 0,
    days: int | None = None,
) -> dict[str, Any]:
    """Run one resource's sync to completion (or its page cap).

    ``days`` is the operator-chosen history window: for date-ranged
    resources (orders, work_orders) it narrows the pull to the last N
    days; roster resources (drivers/trucks/trailers) ignore it.  None
    means "provider default" (work_orders falls back to 90 days, orders
    to no filter), preserving the prior behaviour.

    Returns the final status dict (also left in Redis for pollers).
    Never raises on upstream errors — failures land in the returned
    ``state="failed"`` + ``error`` so fire-and-forget callers don't
    need their own except blocks.
    """
    spec = RESOURCES.get(resource)
    if spec is None:
        raise ValueError(f"unknown datatruck resource {resource!r}")

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status: dict[str, Any] = {
        "state": "running",
        "resource": resource,
        "account_id": account_id,
        "pages_done": 0,
        "records_written": 0,
        "total_upstream": None,
        "started_at": started,
        "finished_at": None,
        "error": None,
        "triggered_by": triggered_by,
        # Per-id detail enrichment outcome (work orders).  detail_failed>0
        # with a detail_error means the token can't reach the detail
        # endpoint — the rich fields (invoice, payment, vendor contact)
        # stay blank and we fell back to the lean list record.
        "detail_ok": 0,
        "detail_failed": 0,
        "detail_error": None,
    }
    await _publish(account_id, resource, status)

    try:
        db = get_platform_db()
        ai = await db.get_account_integration(account_id, _PROVIDER_ID)
        if ai is None or ai.status != "connected":
            raise RuntimeError("datatruck integration is not connected")
        cap_cfg = (ai.feature_toggles or {}).get(spec.capability) or {}
        if not cap_cfg.get("enabled", False):
            raise RuntimeError(
                f"{spec.capability} toggle is disabled — enable it on "
                "the Integration card first",
            )

        tenant = await get_tenant_db(account_id)
        if tenant is None:
            raise RuntimeError("tenant DB unavailable")

        provider = await get_telematics_client(account_id, _PROVIDER_ID)
        client = provider.client  # type: ignore[attr-defined]

        # Work orders reference the vehicle by plate/unit string — pull the
        # rosters live (read-only) once so the page shows the canonical unit
        # number without a separate trucks/trailers sync.
        wo_lookup = (
            await _fetch_vehicle_lookup(account_id)
            if spec.project_work_orders else None
        )

        params = spec.params_factory(days) if spec.params_factory else None
        async for page in client.iter_pages(
            spec.path, params, max_pages=spec.max_pages,
        ):
            if status["total_upstream"] is None:
                status["total_upstream"] = page.get("count")
            records = page.get("results") or []
            # Some resources (work orders) carry far more in their per-id
            # detail view than the list serializer returns — re-fetch each
            # record from detail before normalizing.  Costs one extra
            # rate-gated GET per record; best-effort, so a failed detail
            # fetch falls back to the (leaner) list record rather than
            # dropping the row.
            if spec.detail_path_tmpl:
                records = await _enrich_with_detail(client, spec, records, status)
            normalized = [spec.normalize(r) for r in records if isinstance(r, dict)]
            status["records_written"] += await _persist_rows(
                account_id, spec, normalized, tenant, vehicle_lookup=wo_lookup,
            )
            status["pages_done"] += 1
            # Per-page heartbeat keeps the 5-min staleness timer fed
            # on slow tenants (rate gate can stretch a big sync).
            await _publish(account_id, resource, status)

        status["state"] = "completed"
    except Exception as e:
        logger.exception(
            "datatruck sync failed acct=%d resource=%s", account_id, resource,
        )
        status["state"] = "failed"
        status["error"] = str(e)[:300]
    finally:
        status["finished_at"] = datetime.now(
            timezone.utc,
        ).isoformat(timespec="seconds")
        await _publish(account_id, resource, status)
        logger.info(
            "datatruck sync acct=%d resource=%s state=%s pages=%d records=%d",
            account_id, resource, status["state"],
            status["pages_done"], status["records_written"],
        )
    return status


async def collect_sync_overview(account_id: int) -> dict[str, Any]:
    """Per-resource view for the dashboard card: live Redis status +
    storage stats (count, last_synced_at) + whether the capability
    toggle is enabled."""
    db = get_platform_db()
    ai = await db.get_account_integration(account_id, _PROVIDER_ID)
    toggles = (ai.feature_toggles or {}) if ai else {}
    tenant = await get_tenant_db(account_id)

    out: dict[str, Any] = {}
    for name, spec in RESOURCES.items():
        stats = {"count": 0, "last_synced_at": None}
        if tenant is not None:
            try:
                stats = await getattr(tenant, spec.stats_method)(account_id)
            except Exception as e:
                logger.debug("datatruck stats %s failed: %s", name, e)
        out[name] = {
            "enabled": bool(
                ((toggles.get(spec.capability) or {}).get("enabled", False)),
            ),
            "capability": spec.capability,
            "feature": spec.feature,
            "stored": stats,
            "sync": await get_sync_status(account_id, name),
        }
    return out


# ── Scheduler job factories ──────────────────────────────────────────
#
# The periodic counterpart to the manual "Sync now" button: one auto-sync
# job per resource, fanning out across every account whose toggle for that
# resource's capability is ON.  Reuses the SAME provider-agnostic fan-out
# Samsara's ingest jobs use (``_for_each_account_with_capability``), passing
# ``provider_id="datatruck"`` — so accounts without Datatruck connected, or
# with the toggle off, are skipped (a tick is then a cheap no-op).  The
# cadence is declared once in the catalog (``feature_defaults[cap]
# ["interval_min"]``) and read by the scheduler, so it isn't duplicated here.


def _make_sync_job(resource: str, capability: str):
    """Build the auto-sync scheduler job for one Datatruck resource."""
    async def _sync_one(account_id: int) -> None:
        # sync_resource never raises — a failed account lands in its
        # returned status dict, so one bad token can't abort the fan-out.
        await sync_resource(account_id, resource)
    _sync_one.__name__ = f"datatruck_sync_{resource}"

    async def _job(_app=None) -> None:
        await _for_each_account_with_capability(
            capability, _sync_one, provider_id=_PROVIDER_ID,
        )
    _job.__name__ = f"job_sync_{resource}"
    _job.__doc__ = (
        f"Auto-sync Datatruck {resource} for every account with the "
        f"{capability} toggle enabled."
    )
    return _job


# resource name → job callable, built once from the RESOURCES registry so a
# new resource gets an auto-sync job with zero extra wiring here.
SYNC_JOBS: dict[str, Any] = {
    resource: _make_sync_job(resource, spec.capability)
    for resource, spec in RESOURCES.items()
}
