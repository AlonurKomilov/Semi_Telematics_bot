"""Alert API endpoints."""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.


import asyncio
import re

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel

from interfaces.api.deps import require_permission, require_permission_any, get_tenant_db, get_user_vehicle_nums, paginate, active_view, get_user_company_codes, filter_by_company_map
from capabilities.alerting.service import filter_alerts_by_access

router = APIRouter(prefix="/alerts", tags=["alerts"])

# Strip FMI sub-codes from a fault alert_key so historical variants of
# the *same* SPN (e.g. "524133-31:..." and "524133-9:...") collapse onto
# a single canonical key.  Without this, the dashboard's pending list
# carries every FMI variation as its own row even though they're the
# same logical chronic fault — production data showed 8 variants for
# SPN 524133 alone on one truck.
_FAULT_FMI_RE = re.compile(r"(\d+)-\d+(?=\b|:)")


async def _filter_own(user: dict, alerts: list[dict]) -> list[dict]:
    """If user has only _own permission, filter alerts to their assigned vehicles."""
    if user.get("_matched_perm") != "can_alerts_vehicle":
        return alerts
    trucks = await get_user_vehicle_nums(user)
    return filter_alerts_by_access(alerts, trucks)


def _canonical_key(alert: dict) -> str:
    """Return a dedup key that's stable across FMI variants of the same SPN.

    For fault alerts the raw alert_key embeds every (SPN, FMI) pair the
    truck reported on the cycle the row was created — and Samsara
    flips between FMI 31 / 9 / 19 / combinations for one chronic
    issue.  This canonicaliser strips the FMI suffix and dedups
    repeated SPN segments so all variants share one dedup bucket.
    """
    raw = alert.get("alert_key") or f"{alert.get('vehicle_id')}:{alert.get('alert_type')}"
    if alert.get("alert_type") != "fault":
        return raw
    stripped = _FAULT_FMI_RE.sub(r"\1", raw)
    # Two SPN-31 + SPN-9 segments collapse to "SPN|SPN" of the same SPN —
    # split the key into prefix + segments and dedup the segments.
    head, _, tail = stripped.partition(":")
    if not tail:
        return stripped
    # Walk forward to the first segment that contains an SPN number.
    # alert_key shape is e.g. "OSY:VID:524133:Desc|524133:Desc" — so the
    # vehicle-id segment is the first ":VID:" piece.  Split on "|" to
    # dedup the SPN segments while preserving the prefix.
    prefix_parts = stripped.split(":", 2)
    if len(prefix_parts) < 3:
        return stripped
    prefix = ":".join(prefix_parts[:2])
    body = prefix_parts[2]
    seen: list[str] = []
    for segment in body.split("|"):
        if segment and segment not in seen:
            seen.append(segment)
    return f"{prefix}:{'|'.join(seen)}"


def _dedup_by_alert_key(alerts: list[dict]) -> list[dict]:
    """Deduplicate alerts that share the same canonical alert key.

    The table has one row per subscriber (chat_id) per alert, plus one
    row per FMI variant for fault alerts.  For the dashboard we want
    one row per logical alert, keeping the row with the highest-priority
    status (active > acknowledged > expired) and the latest created_at.
    """
    STATUS_RANK = {"active": 0, "acknowledged": 1, "expired": 2, "superseded": 3, "info": 4}
    seen: dict[str, dict] = {}
    for a in alerts:
        key = _canonical_key(a)
        if key not in seen:
            seen[key] = a
        else:
            existing = seen[key]
            # Prefer higher-priority status
            if STATUS_RANK.get(a.get("status"), 9) < STATUS_RANK.get(existing.get("status"), 9):
                seen[key] = a
            elif a.get("created_at", "") > existing.get("created_at", ""):
                seen[key] = a
    return list(seen.values())


def _shape_history_for_pending_api(row: dict) -> dict:
    """Translate an `alert_history` row into the shape the dashboard +
    mini-app already expect for the pending list.

    Carries through:
        id              → AlertID surfaced in UI ("#1234")
        alert_type, vehicle_id, vehicle_name
        occurrence_count → "× 5 occurrences"
        first_seen, last_seen, last_detail
        status (always 'active' here)
        message          ← derived from last_detail for backward compat
        created_at       ← maps to first_seen so existing UI sorting works
        alert_key        ← stable canonical id (history.id-based)
    """
    return {
        "id": row.get("id"),
        "alert_type": row.get("alert_type"),
        "vehicle_id": row.get("vehicle_id"),
        "vehicle_name": row.get("vehicle_name"),
        # Severity + location are now SSOT on alert_history (writes by
        # pipeline.send_alert).  Frontends should read these instead of
        # re-deriving from alert_type.
        "severity": (row.get("severity") or "warning"),
        "location": row.get("location") or "",
        "occurrence_count": row.get("occurrence_count") or 1,
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "created_at": row.get("first_seen"),
        "last_detail": row.get("last_detail") or "",
        "message": row.get("last_detail") or "",
        "status": row.get("status") or "active",
        # Ack attribution for the windowed dashboard view:
        #   acknowledged_by > 0 + name → "Acknowledged by {name}"
        #   cleared with no acknowledged_by → "Auto-resolved"
        "acknowledged_by": row.get("acknowledged_by"),
        "acknowledged_at": row.get("acknowledged_at"),
        "acknowledged_by_name": row.get("acknowledged_by_name") or "",
        # Stable canonical key for clients that still cache by alert_key —
        # using the history.id makes it stable across re-fires.
        "alert_key": f"hist:{row.get('id')}",
    }


def _norm_ack_state(value: str | None) -> str:
    """Normalise the dashboard's status chip to the DB's ack_state.

    'all' / 'acknowledged' pass through; anything else (including the
    default and unknown values) collapses to 'active' so a missing or
    garbled param can never silently widen the result to acked rows.
    """
    v = (value or "").lower()
    return v if v in ("all", "acknowledged") else "active"


async def _vehicle_company_map(account_id: int, tenant_db) -> dict:
    """``vehicle_id → company_code`` for the account, so alerts (which
    carry no company column) can be company-filtered via their vehicle.

    Keyed by ``vehicle_id`` (globally unique), NOT ``vehicle_name`` —
    names collide across companies (e.g. truck "103" in two companies),
    which would silently mis-map one and hide its alerts.

    Best-effort: any read failure returns ``{}`` → ``filter_by_company_map``
    fail-opens (shows all) rather than hiding every alert.
    """
    try:
        states = await tenant_db.get_vehicle_state(account_id)
        return {s.get("vehicle_id"): s.get("company_code") for s in states}
    except Exception:
        return {}


@router.get("/pending")
async def pending_alerts(
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    severity: str | None = Query(None, description="Filter: critical, warning, info"),
    # Ack-state chip: 'active' (not acknowledged, default), 'acknowledged'
    # (human-acked or auto-resolved), or 'all'.
    ack_state: str | None = Query(None, description="active | acknowledged | all"),
    # Date window on first_seen — 'active' rows ignore it in practice
    # (an active alert is current), but the acknowledged/all views use
    # it so "last 30 days" actually bounds the historical set.
    days: int | None = Query(None, ge=1, le=90),
    page: int = Query(1, ge=1, description="Page number"),
    # Cap raised 200 → 500 so a fleet with 300+ active logical alerts
    # can render in one round-trip on the dashboard / mini-app.  The
    # default stays 50 for backwards compat with any client that
    # doesn't pass page_size.
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Get logical alerts for this account within the requested window.

    Reads from `alert_history` (one row per (account, alert_type, vehicle))
    instead of `alert_acknowledgments` (one row per delivery × subscriber).
    Production data shows alert_acknowledgments has ~33× more rows than
    alert_history because every re-fire to every subscriber adds a row;
    `alert_history` is the SSOT and gives one entry per logical issue.

    ``ack_state`` selects un-acked (default), acknowledged, or all;
    ``days`` windows the acknowledged/all views on first_seen.
    """
    state = _norm_ack_state(ack_state)
    # Drivers (own-truck) AND company-restricted users both need the
    # PYTHON path: their filters can't run in SQL — alert_history has no
    # company column, and the truck filter joins user→vehicle in Python.
    # So fetch the full filtered set, filter, then paginate.  Everyone
    # else gets the faster SQL-paginated path below.
    is_driver_scope = user.get("_matched_perm") == "can_alerts_vehicle"
    allowed = await get_user_company_codes(user)
    if is_driver_scope or allowed:
        rows = await tenant_db.get_active_alert_history_for_account_paged(
            user["account_id"],
            alert_type=alert_type, vehicle_substring=vehicle,
            severity=severity, ack_state=state, days=days,
        )
        alerts = [_shape_history_for_pending_api(r) for r in rows]
        if is_driver_scope:
            alerts = await _filter_own(user, alerts)
        if allowed:
            veh_map = await _vehicle_company_map(user["account_id"], tenant_db)
            alerts = filter_by_company_map(alerts, allowed, veh_map, key="vehicle_id")
        paged = paginate(alerts, page, page_size)
        return {"alerts": paged["items"], "count": paged["total"],
                "page": paged["page"], "page_size": paged["page_size"],
                "total_pages": paged["total_pages"]}

    # Non-driver path: push everything to SQL.
    offset = (page - 1) * page_size
    total = await tenant_db.count_active_alert_history_for_account_filtered(
        user["account_id"], alert_type=alert_type, vehicle_substring=vehicle,
        severity=severity, ack_state=state, days=days,
    )
    rows = await tenant_db.get_active_alert_history_for_account_paged(
        user["account_id"],
        alert_type=alert_type, vehicle_substring=vehicle,
        severity=severity, ack_state=state, days=days,
        limit=page_size, offset=offset,
    )
    alerts = [_shape_history_for_pending_api(r) for r in rows]
    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    return {"alerts": alerts, "count": total,
            "page": page, "page_size": page_size,
            "total_pages": total_pages}


@router.get("/pending/by-vehicle")
async def pending_alerts_by_vehicle(
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    severity: str | None = Query(None, description="Filter: critical, warning, info"),
    ack_state: str | None = Query(None, description="active | acknowledged | all"),
    days: int | None = Query(None, ge=1, le=90),
    page: int = Query(1, ge=1, description="Page number (over vehicles, not alerts)"),
    page_size: int = Query(50, ge=1, le=200, description="Vehicles per page (max 200)"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Per-vehicle aggregated view of alerts within the window.

    Pagination is over **vehicles**, not alert rows — so an account
    with 2000 alerts spread across 80 trucks paginates as
    ``Page 1 of 2`` (vehicles), not ``Page 1 of 22`` (alerts).
    Each vehicle carries its alert list embedded so the front-end's
    expand affordance stays a pure client toggle.  ``ack_state`` and
    ``days`` mirror the per-alert /pending endpoint.
    """
    state = _norm_ack_state(ack_state)
    is_driver_scope = user.get("_matched_perm") == "can_alerts_vehicle"
    offset = (page - 1) * page_size
    vehicles, total = await tenant_db.get_active_vehicles_with_alerts_paged(
        user["account_id"],
        alert_type=alert_type, vehicle_substring=vehicle,
        severity=severity, ack_state=state, days=days,
        limit=page_size, offset=offset,
    )
    # Reshape embedded alerts for the same dashboard surface as /pending.
    for v in vehicles:
        v["alerts"] = [_shape_history_for_pending_api(a) for a in v["alerts"]]

    if is_driver_scope:
        trucks = await get_user_vehicle_nums(user)
        if trucks is not None:
            allowed = {(t or "").lower() for t in trucks}
            vehicles = [
                v for v in vehicles
                if (v.get("vehicle_name") or "").lower() in allowed
            ]
            # After driver isolation we no longer know the true vehicle
            # total without a second query; clamp to what we hand back
            # so the footer's "of N vehicles" stays consistent.
            total = min(total, len(vehicles)) if total else len(vehicles)

    # Company scoping — restrict to the user's allowed companies (alerts
    # carry no company column, so resolve via the vehicle).  Same clamp
    # caveat as driver isolation above.
    company_codes = await get_user_company_codes(user)
    if company_codes:
        veh_map = await _vehicle_company_map(user["account_id"], tenant_db)
        before = len(vehicles)
        vehicles = filter_by_company_map(vehicles, company_codes, veh_map, key="vehicle_id")
        if len(vehicles) != before:
            total = min(total, len(vehicles)) if total else len(vehicles)

    total_pages = max(1, (total + page_size - 1) // page_size) if total > 0 else 1
    return {
        "vehicles": vehicles,
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/aggregate")
async def alerts_aggregate(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Histogram of alert counts by alert_type for the last N days.

    Powers the owner/admin AccountAlertSummary card — small, dependable,
    server-aggregated so the dashboard doesn't have to walk a page of
    rows to render a one-line bar chart.

    Respects the same driver-scope rule the queue uses: ``_own`` users
    only count their assigned trucks' alerts; ``_all`` users see the
    full account aggregate.
    """
    is_driver_scope = user.get("_matched_perm") == "can_alerts_vehicle"

    if is_driver_scope:
        rows = await tenant_db.get_active_alert_history_for_account(user["account_id"])
        alerts = [_shape_history_for_pending_api(r) for r in rows]
        alerts = await _filter_own(user, alerts)
    else:
        # Pull the full active set bounded by ``days`` so the histogram
        # reflects recent activity, not the lifetime of the account.
        # Same SQL path the dashboard's /pending uses with no extra
        # filters, just the window.
        rows = await tenant_db.get_active_alert_history_for_account_paged(
            user["account_id"], days=days,
        )
        alerts = [_shape_history_for_pending_api(r) for r in rows]

    # Company scope: a restricted user's histogram counts only their
    # companies' alerts (resolved via vehicle; fail-open if cold/unknown).
    company_codes = await get_user_company_codes(user)
    if company_codes:
        veh_map = await _vehicle_company_map(user["account_id"], tenant_db)
        alerts = filter_by_company_map(alerts, company_codes, veh_map, key="vehicle_id")

    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for a in alerts:
        t = a.get("alert_type") or "unknown"
        s = (a.get("severity") or "unknown").lower()
        by_type[t] = by_type.get(t, 0) + 1
        by_severity[s] = by_severity.get(s, 0) + 1

    return {
        "by_type":     by_type,
        "by_severity": by_severity,
        "total":       len(alerts),
        "days":        days,
    }


@router.get("/pending/count")
async def pending_alerts_count(
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
    view_role: str = Depends(active_view),
):
    """Lightweight count of pending alerts respecting driver isolation
    AND strict persona scoping.

    Used by the miniapp tab badge poll — avoids serialising the full
    paginated list when only the count is needed.  Reads
    `alert_history` (logical alerts) for consistency with /pending.

    Strict binding: every role counts only alerts in its own persona's
    type set.  Owner / Admin's persona (owner_admin) has no
    operational types, so their badge count is 0 — they don't ack
    alerts directly; they review escalations.  To do operational
    triage Owner/Admin switch view to Fleet/Safety/etc. via the
    persona selector and the X-View-As header re-scopes the count.
    """
    is_driver_scope = user.get("_matched_perm") == "can_alerts_vehicle"
    if is_driver_scope:
        # Driver scope still needs the row list because access filtering
        # joins user→vehicle assignments.  Their truck count is small,
        # so the full fetch is cheap here.  No persona filter — driver
        # gets EVERYTHING about their own truck.
        rows = await tenant_db.get_active_alert_history_for_account(user["account_id"])
        alerts = [_shape_history_for_pending_api(r) for r in rows]
        alerts = await _filter_own(user, alerts)
        return {"count": len(alerts)}

    from capabilities.alerting import persona_mapping
    allowed_types = persona_mapping.alert_types_for_role(view_role)
    if not allowed_types:
        # No operational alert types for this active view (owner /
        # admin / accounting / unknown) — badge is 0.  The dashboard
        # tab won't surface a misleading count for a role that
        # doesn't actually triage alerts at this scope.
        return {"count": 0}

    # Company scope: a restricted user can't use the SQL COUNT fast path
    # (alert_history has no company column).  Fetch, filter by persona +
    # company (resolved via vehicle), and count the rows instead.
    company_codes = await get_user_company_codes(user)
    if company_codes:
        rows = await tenant_db.get_active_alert_history_for_account(user["account_id"])
        alerts = [_shape_history_for_pending_api(r) for r in rows]
        type_set = set(allowed_types)
        alerts = [a for a in alerts if (a.get("alert_type") or "") in type_set]
        veh_map = await _vehicle_company_map(user["account_id"], tenant_db)
        alerts = filter_by_company_map(alerts, company_codes, veh_map, key="vehicle_id")
        return {"count": len(alerts)}

    # Persona-filtered fast path: sum filtered COUNT(*) per type.  The
    # type set per persona is small (≤6 entries) so this stays sub-ms
    # in practice — way cheaper than fetching every row and filtering
    # in Python.
    total = 0
    for at in allowed_types:
        total += await tenant_db.count_active_alert_history_for_account_filtered(
            user["account_id"], alert_type=at,
        )
    return {"count": total}


@router.get("/history")
async def alert_history(
    days: int = Query(7, ge=1, le=90),
    alert_type: str | None = Query(None, description="Filter: fault, health, fuel, events"),
    vehicle: str | None = Query(None, description="Filter by vehicle name (substring)"),
    status: str | None = Query(None, description="Filter: acknowledged, expired, active"),
    severity: str | None = Query(None, description="Filter: critical, warning, info"),
    page: int = Query(1, ge=1, description="Page number"),
    # Cap raised 200 → 500 so a fleet with 300+ active logical alerts
    # can render in one round-trip on the dashboard / mini-app.  The
    # default stays 50 for backwards compat with any client that
    # doesn't pass page_size.
    page_size: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Get alert history for this account.

    Filters (alert_type / vehicle / status) are applied in SQL so the
    Python side only sees the narrowed window — meaningful when an
    account has thousands of historical rows.
    """
    # Cap the SQL window at days*50 to keep the response bounded;
    # downstream pagination then carves the result into pages.
    alerts = await tenant_db.get_alert_history(
        user["account_id"],
        limit=days * 50,
        alert_type=alert_type,
        vehicle_substring=vehicle,
        status=status,
        severity=severity,
    )
    alerts = await _filter_own(user, alerts)
    company_codes = await get_user_company_codes(user)
    if company_codes:
        veh_map = await _vehicle_company_map(user["account_id"], tenant_db)
        alerts = filter_by_company_map(alerts, company_codes, veh_map, key="vehicle_id")
    alerts = _dedup_by_alert_key(alerts)

    paged = paginate(alerts, page, page_size)
    return {"alerts": paged["items"], "count": paged["total"],
            "page": paged["page"], "page_size": paged["page_size"],
            "total_pages": paged["total_pages"]}


@router.post("/{ack_id}/acknowledge")
async def acknowledge_alert(
    ack_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Acknowledge an alert from the web UI.

    `ack_id` is now an `alert_history.id` — the canonical AlertID that
    the /pending list returns.  The ack clears the history row and
    cascades to every delivery receipt (alert_acknowledgments) that
    belongs to the same (account, alert_type, vehicle).  A single click
    silences the whole logical alert across every recipient instead of
    requiring per-recipient acks.

    Falls back to the legacy per-delivery ack path when the supplied id
    matches an `alert_acknowledgments` row instead — preserves backward
    compatibility for any old client that still sends ack_ids.
    """
    telegram_id = int(user["sub"])
    cleared = await tenant_db.acknowledge_alert_history(
        ack_id, telegram_id, account_id=user["account_id"],
    )
    if cleared:
        return {"status": "acknowledged", "ack_id": ack_id, "scope": "history"}
    # Legacy fallback — old clients sending alert_acknowledgments.id.
    success = await tenant_db.acknowledge_alert(ack_id, telegram_id, account_id=user["account_id"])
    if not success:
        raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")
    return {"status": "acknowledged", "ack_id": ack_id, "scope": "legacy"}


class BulkAckRequest(BaseModel):
    ids: list[int]


async def _ack_one(tenant_db, alert_id: int, telegram_id: int, account_id: int) -> bool:
    """Try the new history-ack first; fall back to per-delivery ack."""
    cleared = await tenant_db.acknowledge_alert_history(alert_id, telegram_id, account_id=account_id)
    if cleared:
        return True
    return await tenant_db.acknowledge_alert(alert_id, telegram_id, account_id=account_id)


@router.post("/bulk-ack")
async def bulk_acknowledge(
    body: BulkAckRequest,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Acknowledge multiple alerts at once."""
    telegram_id = int(user["sub"])
    if not body.ids:
        return {"acked": 0, "failed": 0, "total": 0}
    # Run all acknowledges in parallel — N sequential awaits become 1 round trip
    # per row served concurrently by the asyncpg pool.
    results = await asyncio.gather(
        *(
            _ack_one(tenant_db, ack_id, telegram_id, user["account_id"])
            for ack_id in body.ids
        ),
        return_exceptions=True,
    )
    acked = sum(1 for r in results if r is True)
    failed = len(results) - acked
    return {"acked": acked, "failed": failed, "total": len(body.ids)}


# ── D2: per-alert mute ─────────────────────────────────────────────

class MuteRequest(BaseModel):
    hours: int = 24 * 7   # default 7-day mute
    reason: str = ""


@router.post("/{history_id}/mute")
async def mute_alert(
    history_id: int,
    body: MuteRequest,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Mute Telegram delivery for a specific alert (alert_history.id).

    The dashboard / mini-app still show the alert as active — only the
    Telegram pings stop until ``muted_until`` passes.  CRITICAL alerts
    ignore mutes (handled in pipeline.send_alert) so a real fire still
    pages someone.
    """
    if body.hours <= 0 or body.hours > 24 * 30:
        raise HTTPException(status_code=400, detail="hours must be in 1..720")
    telegram_id = int(user["sub"])
    mute = await tenant_db.mute_alert_history(
        history_id, account_id=user["account_id"],
        muted_by=telegram_id, hours=body.hours, reason=body.reason,
    )
    if not mute:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"status": "muted", **mute}


@router.delete("/{history_id}/mute")
async def unmute_alert(
    history_id: int,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Drop every active mute on this alert — Telegram delivery resumes
    on the next pipeline cycle."""
    removed = await tenant_db.unmute_alert_history(
        history_id, account_id=user["account_id"],
    )
    return {"status": "unmuted", "removed": removed}


@router.get("/mutes")
async def list_active_mutes(
    limit: int = Query(500, ge=1, le=2000),
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """List active (unexpired) mutes for this account so the dashboard
    can render a 🔇 badge. Capped to ``limit`` (default 500); accounts
    with more pending mutes paginate through subsequent calls."""
    mutes = await tenant_db.get_active_mutes_for_account(
        user["account_id"], limit=limit,
    )
    return {"mutes": mutes, "count": len(mutes)}


# ── D3: per-vehicle bulk-ack ───────────────────────────────────────

@router.post("/vehicle/{vehicle_id}/ack")
async def acknowledge_vehicle_alerts(
    vehicle_id: str,
    user: dict = Depends(require_permission_any("can_alerts_all", "can_alerts_vehicle")),
    tenant_db=Depends(get_tenant_db),
):
    """Acknowledge every active alert for one vehicle in one click.

    Replaces the destructive "Ack all 359 alerts globally" UX with the
    safer "Ack the 5 alerts for Truck 200" — same 1-click ergonomics,
    much smaller blast radius.
    """
    telegram_id = int(user["sub"])
    # Push the vehicle filter into SQL — previously this loaded every
    # active alert for the account and filtered in Python.
    targets = await tenant_db.get_active_alert_history_for_vehicle(
        user["account_id"], str(vehicle_id),
    )
    if not targets:
        return {"acked": 0, "vehicle_id": vehicle_id}

    # Driver isolation: only let drivers ack alerts for their own truck(s).
    if user.get("_matched_perm") == "can_alerts_vehicle":
        from interfaces.api.deps import get_user_vehicle_nums
        allowed = await get_user_vehicle_nums(user)
        if allowed is not None:
            allowed_lower = {(t or "").lower() for t in allowed}
            vname_match = (targets[0].get("vehicle_name") or "").lower()
            if vname_match and vname_match not in allowed_lower:
                raise HTTPException(status_code=403, detail="Vehicle not in your assignments")

    results = await asyncio.gather(
        *(
            tenant_db.acknowledge_alert_history(r["id"], telegram_id, account_id=user["account_id"])
            for r in targets
        ),
        return_exceptions=True,
    )
    acked = sum(1 for r in results if r and not isinstance(r, BaseException))
    return {"acked": acked, "total": len(targets), "vehicle_id": vehicle_id}



# ═══ Per-user alert preferences — My Notifications (an Alerts component) ═══════════════════════════════════════════════════
# Extracted from the governance router — these endpoints belong to THIS
# domain (docs/FEATURES.md feature→component tree).  URLs unchanged.
from typing import Optional
from interfaces.api.deps import (  # noqa: F811 — section-local completeness
    get_current_db_user, get_current_user, get_platform_db,
    get_tenant_db, require_permission,
)
user_router = APIRouter(prefix="/user", tags=["user"])

# ── Personal alert preferences (per-user DM toggles) ───────────────
#
# These endpoints power the dashboard "My Notifications" page
# (avatar menu → My Notifications).  Each user owns their own
# per-alert-type DM toggle + the new "🟢 Resolve receipts" opt-in
# (migration 080).  The toggle list is role-tailored — a Safety
# user doesn't see a Fuel toggle; a Dispatcher doesn't see Health.
# See ``capabilities/alerting/relevance.py`` for the role → type
# mapping.
#
# Admin-side per-topic config (group/forum routing + resolve-
# receipt toggle per topic) lives separately in
# ``interfaces/api/routes/admin.py``; the two surfaces are
# independent — neither overrides the other.


class AlertPrefsRequest(BaseModel):
    """Per-user alert preferences PATCH body.

    Every field is optional so the dashboard can send partial
    updates (toggle one switch at a time).  Unknown alert types are
    silently ignored at the adapter layer so a stale UI doesn't
    500 the request.
    """
    alerts_on: Optional[bool] = None
    alert_faults: Optional[bool] = None
    alert_health: Optional[bool] = None
    alert_fuel: Optional[bool] = None
    alert_geofence: Optional[bool] = None
    alert_events: Optional[bool] = None
    alert_parking: Optional[bool] = None
    alert_camera: Optional[bool] = None
    alert_resolve_receipts: Optional[bool] = None


@user_router.get("/me/alerts")
async def get_my_alerts(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Return the role-tailored alert preferences for the current user.

    Response shape::

        {
          "alerts_on": true,
          "alert_resolve_receipts": false,
          "relevant_types": ["faults", "health", "fuel", ...],
          "toggles": {
            "alert_faults": true,
            "alert_health": true,
            ...  // only includes relevant types
          }
        }

    The ``relevant_types`` list drives which toggles the dashboard
    renders.  ``toggles`` mirrors that filter so the client never
    sees a stale value for an irrelevant type.
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    from capabilities.alerting.relevance import alert_types_for_role
    relevant = alert_types_for_role(db_user.role)
    toggles: dict[str, bool] = {}
    for atype in relevant:
        attr = f"alert_{atype}"
        toggles[attr] = bool(getattr(db_user, attr, True))
    return {
        "alerts_on": bool(db_user.alerts_on),
        "alert_resolve_receipts": bool(
            getattr(db_user, "alert_resolve_receipts", False)
        ),
        "relevant_types": relevant,
        "toggles": toggles,
    }


@user_router.put("/me/alerts")
async def update_my_alerts(
    body: AlertPrefsRequest,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Patch the current user's alert preferences.

    Role-tailored: requests to toggle an alert type the user's role
    doesn't have permission for are silently dropped (the dashboard
    UI shouldn't render that toggle anyway, but this is defense in
    depth so a crafted request can't enable irrelevant alerts).
    """
    db_user = await get_current_db_user(user, platform_db)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    from capabilities.alerting.relevance import alert_types_for_role
    relevant_attrs = {
        f"alert_{atype}" for atype in alert_types_for_role(db_user.role)
    }
    # ``alerts_on`` (master switch) and ``alert_resolve_receipts``
    # apply regardless of role — every role can opt in/out of these.
    relevant_attrs.add("alerts_on")
    relevant_attrs.add("alert_resolve_receipts")

    updates: dict[str, bool] = {}
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if field not in relevant_attrs:
            # Silently drop irrelevant toggles instead of 422'ing —
            # the dashboard might briefly include a stale field
            # during a role-change race, and we don't want to error
            # the whole save for that.
            continue
        updates[field] = bool(value)

    if updates:
        await platform_db.update_user(db_user.id, **updates)

    # Echo the post-update state so the dashboard re-syncs without
    # a second roundtrip.
    fresh = await platform_db.get_user_by_id(db_user.id) if hasattr(
        platform_db, "get_user_by_id"
    ) else db_user
    relevant = alert_types_for_role(fresh.role if fresh else db_user.role)
    toggles: dict[str, bool] = {}
    target = fresh or db_user
    for atype in relevant:
        attr = f"alert_{atype}"
        toggles[attr] = bool(getattr(target, attr, True))
    return {
        "alerts_on": bool(target.alerts_on),
        "alert_resolve_receipts": bool(
            getattr(target, "alert_resolve_receipts", False)
        ),
        "relevant_types": relevant,
        "toggles": toggles,
    }


# ── Escalations summary (was GET /admin/escalations until 2026-06-11;
#    it's an alerting read consumed by the alerts EscalationStatusCard) ──

@router.get("/escalations")
async def escalation_summary(
    user: dict = Depends(require_permission("can_alerts_all")),
    tenant_db=Depends(get_tenant_db),
):
    """Owner/admin oversight: how many active alerts are past their
    re-escalation window or have hit the max-attempts cap.

    Computed from ``alert_history`` (one row per logical alert) and
    the env-tuned re-escalation knobs.  ``past_due`` means the alert
    is older than ``REESCALATE_AFTER_MINUTES`` and unacked (the
    pipeline is currently re-pinging it).  ``breached`` means it hit
    ``REESCALATE_MAX_ATTEMPTS`` and the pipeline stopped paging — the
    alert is still in the dashboard queue but no longer interrupting
    operators.  Both counts are CRITICAL/WARNING only.

    The ``by_persona`` map groups past_due counts by the persona that
    owns each alert_type (per ``capabilities.alerting.persona_mapping``)
    so the EscalationStatusCard can drill the owner directly into
    "Dispatch has 5 alerts past due" without scanning the queue.
    """
    from datetime import datetime, timezone, timedelta
    from infra.config import (
        REESCALATE_AFTER_MINUTES, REESCALATE_MAX_ATTEMPTS,
    )
    from capabilities.alerting import persona_mapping

    account_id = user["account_id"]
    rows = await tenant_db.get_active_alert_history_for_account(account_id)

    now = datetime.now(timezone.utc)
    cutoff_minutes = max(REESCALATE_AFTER_MINUTES, 0)
    cutoff = now - timedelta(minutes=cutoff_minutes)

    past_due = 0
    breached = 0
    by_persona: dict[str, int] = {}
    for r in rows:
        sev = (r.get("severity") or "").lower()
        if sev not in ("critical", "warning"):
            continue
        # `last_seen` is the latest re-fire timestamp — use it as the
        # age anchor so a chronic alert that fired again 5 minutes ago
        # isn't flagged "past_due" just because its first_seen is old.
        last_seen = r.get("last_seen") or r.get("first_seen") or ""
        try:
            ts = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        reesc = int(r.get("reescalate_count") or 0)
        is_past_due = cutoff_minutes > 0 and ts < cutoff
        if is_past_due:
            past_due += 1
            persona = persona_mapping.persona_for_alert(r.get("alert_type") or "")
            by_persona[persona] = by_persona.get(persona, 0) + 1
        if reesc >= REESCALATE_MAX_ATTEMPTS:
            breached += 1

    return {
        "past_due_count":   past_due,
        "breached_count":   breached,
        "by_persona":       by_persona,
        # Knobs returned so the UI can render a "older than 60m" label
        # without reading env from the browser.
        "reescalate_after_minutes": REESCALATE_AFTER_MINUTES,
        "reescalate_max_attempts":  REESCALATE_MAX_ATTEMPTS,
    }
