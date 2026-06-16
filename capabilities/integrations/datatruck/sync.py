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
    first = _as_text(_first(rec, "first_name", "firstName"))
    last = _as_text(_first(rec, "last_name", "lastName"))
    display = _as_text(_first(rec, "name", "full_name", "fullName"))
    if not display:
        display = f"{first} {last}".strip()
    return {
        "external_id":  _as_id(rec.get("id")),
        "first_name":   first,
        "last_name":    last,
        "display_name": display,
        "phone":        _as_text(_first(rec, "phone", "phone_number", "phoneNumber")),
        "email":        _as_text(_first(rec, "email", "email_address")),
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


def _norm_order(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id":        _as_id(rec.get("id")),
        "order_number":       _as_text(_first(
            rec, "order_number", "orderNumber", "number", "load_number",
        )) or _as_id(rec.get("id")),
        "status":             _as_text(_first(rec, "status", "state"), "name"),
        "pickup_date":        _as_text(_first(
            rec, "pickup_date", "pickupDate", "pickup_at", "start_date",
        )),
        "delivery_date":      _as_text(_first(
            rec, "delivery_date", "deliveryDate", "delivered_at", "end_date",
        )),
        "origin":             _as_text(
            _first(rec, "origin", "pickup_location", "from_location"),
            "name", "city", "address",
        ),
        "destination":        _as_text(
            _first(rec, "destination", "delivery_location", "to_location"),
            "name", "city", "address",
        ),
        "driver_external_id": _as_id(_first(rec, "driver", "driver_id")),
        "truck_external_id":  _as_id(_first(rec, "truck", "truck_id")),
        "total_rate":         _as_float(_first(
            rec, "total_rate", "totalRate", "rate", "total",
        )),
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
    Note the WO **number** ("WO-00991") and the **invoice id** ("46478")
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


RESOURCES: dict[str, ResourceSpec] = {
    spec.name: spec for spec in (
        ResourceSpec(
            name="drivers", path="drivers/list/",
            capability=Capability.TMS_DRIVERS_SYNC,
            normalize=_norm_driver,
            upsert_method="upsert_datatruck_drivers",
            stats_method="datatruck_drivers_stats",
            max_pages=30,
        ),
        ResourceSpec(
            name="trucks", path="trucks/list/",
            capability=Capability.TMS_TRUCKS_SYNC,
            normalize=_norm_truck,
            upsert_method="upsert_datatruck_trucks",
            stats_method="datatruck_trucks_stats",
            max_pages=30,
            registry_vehicle_type="truck",
        ),
        ResourceSpec(
            name="trailers", path="trailers/list/",
            capability=Capability.TMS_TRAILERS_SYNC,
            normalize=_norm_trailer,
            upsert_method="upsert_datatruck_trailers",
            stats_method="datatruck_trailers_stats",
            max_pages=30,
            registry_vehicle_type="trailer",
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
        upsert = getattr(tenant, spec.upsert_method)

        params = spec.params_factory(days) if spec.params_factory else None
        async for page in client.iter_pages(
            spec.path, params, max_pages=spec.max_pages,
        ):
            if status["total_upstream"] is None:
                status["total_upstream"] = page.get("count")
            records = page.get("results") or []
            normalized = [spec.normalize(r) for r in records if isinstance(r, dict)]
            status["records_written"] += await upsert(account_id, normalized)
            # Project trucks/trailers into the Vehicle registry (SSOT)
            # so Datatruck-synced equipment appears on the Vehicles page
            # — reconciled against Samsara/manual rows by VIN, else unit.
            # Best-effort: a registry hiccup must not fail the TMS sync.
            if spec.registry_vehicle_type:
                try:
                    await tenant.project_external_vehicles(
                        account_id, normalized,
                        vehicle_type=spec.registry_vehicle_type,
                        source="datatruck",
                    )
                except Exception as e:
                    logger.debug(
                        "datatruck registry projection skipped acct=%d: %s",
                        account_id, e,
                    )
            # Project work orders into the module work_orders table (SSOT)
            # so synced shop invoices appear on the Work Orders page.
            # Best-effort: a projection hiccup must not fail the TMS sync.
            if spec.project_work_orders:
                try:
                    await tenant.project_external_work_orders(
                        account_id, normalized, source="datatruck",
                    )
                except Exception as e:
                    logger.debug(
                        "datatruck work-order projection skipped acct=%d: %s",
                        account_id, e,
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
            "stored": stats,
            "sync": await get_sync_status(account_id, name),
        }
    return out
