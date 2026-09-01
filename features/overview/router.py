"""Overview stats API — the landing page's aggregated numbers.

URL history: served at /fleet/overview/stats (with a /dashboard/stats
alias) until 2026-06-11; now /overview/stats, no aliases.
"""

# router code is interface-layer, co-located with its owner
# (docs/FEATURES.md): ONLY router files import interfaces.api.deps.

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Query

from interfaces.api.deps import (
    require_permission,
    get_current_user,
    get_user_company_codes,
    get_user_vehicle_nums,
    get_tenant_db,
    validate_company_access,
    filter_by_allowed_companies,
    filter_by_assigned_trucks,
    active_view,
)
from adapters.storage import Role
from capabilities.permissions.roles import can
from features.vehicles.service import get_vehicles_overview as _svc_vehicles_overview
from features.vehicles.warehouse.service import get_fleet_weather as _svc_fleet_weather
from features.vehicles.warehouse import readers as _wh_reader
from features.location.service import classify_vehicle_status, get_vehicles_for_map

router = APIRouter(prefix="/overview", tags=["overview"])


# ── overview/stats per-account TTL cache ─────────────────────────────────────
# The stats endpoint is hit by every Hero strip on every open dashboard, plus
# the Overview page itself.  N concurrent users on one account would otherwise
# each pay the full cost of (a) get_vehicles_for_map + (b) three DB reads, even
# though the result is identical for everyone sharing the same role and
# company filter.  A 15s in-process cache collapses that to one underlying
# fetch per cache window per (account, role, company, companies-allowed)
# tuple.  Matches the cadence of _engine_states_cache in location/service.py
# so freshness stays uniform across the chain.
#
# Driver responses are NOT cached — they're per-user (filtered to assigned
# trucks, include my_truck / my_alerts), and the upstream warehouse read is
# already cheap for a single-vehicle slice.
_STATS_TTL = 15.0
_stats_cache: dict[tuple, tuple[float, dict]] = {}


def _stats_cache_key(
    account_id: int,
    role: str,
    company: str | None,
    allowed: list[str],
) -> tuple:
    return (account_id, role, company or "", tuple(sorted(allowed)))


def _stats_cache_get(key: tuple) -> dict | None:
    entry = _stats_cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _STATS_TTL:
        return entry[1]
    return None


def _stats_cache_put(key: tuple, value: dict) -> None:
    _stats_cache[key] = (time.monotonic(), value)
    # Cheap bound — at >1000 keys, evict anything past TTL.  Acceptable
    # for a single FastAPI worker; cross-worker sharing would need Redis.
    if len(_stats_cache) > 1000:
        cutoff = time.monotonic() - _STATS_TTL
        for k, v in list(_stats_cache.items()):
            if v[0] < cutoff:
                _stats_cache.pop(k, None)




@router.get("/stats")
async def overview_stats(
    company: str | None = Query(None),
    user: dict = Depends(get_current_user),
    tenant_db=Depends(get_tenant_db),
    view_role: str = Depends(active_view),
):
    """Aggregated fleet stats for the overview page (role-aware).

    ``view_role`` is the role the operator is operating AS right now
    (X-View-As header).  Owner/admin can switch view to Fleet/Safety/
    etc. via the dashboard's persona selector; this endpoint scopes
    its persona-aware counts (pending_alerts) to that active view so
    the Overview hero card matches the workspace the SPA is showing.
    """
    account_id = user["account_id"]
    # JWT payloads store role as a string; coerce to the str-backed enum so
    # type-checkers see a real Role and downstream ``can()`` calls type-check
    # against their declared signature.
    role = Role(user.get("role", "driver"))

    allowed = await get_user_company_codes(user)
    validate_company_access(allowed, company)

    # Cache lookup before any work.  Driver responses are per-user and
    # not cached (see _STATS_TTL comment); every other role shares the
    # same shape within (account, role, company, allowed) so one
    # successful compute serves every Hero strip on every dashboard for
    # the next _STATS_TTL seconds.
    #
    # The cache key includes ``view_role`` so an Owner switching to
    # Fleet view doesn't get a stale Owner-view cached response; the
    # backend computes a fresh slice for each (real role, view role)
    # pair.  Same TTL applies — cache hit rate is unchanged because
    # each role+view combination is independently re-hot.
    cache_key = None
    if role != Role.DRIVER:
        cache_key = _stats_cache_key(account_id, f"{role}:{view_role}", company, allowed)
        cached = _stats_cache_get(cache_key)
        if cached is not None:
            return cached

    # Fan out the four independent reads concurrently. Without this, the
    # Samsara fetch (~200-500ms) blocks the three DB reads (~30-50ms each),
    # forcing the user to wait ~350ms instead of ~250ms for the Overview page.
    # Skip the role-gated calls so a driver doesn't open three connections
    # only to discard the results.
    fetch_alerts = (
        role == Role.DRIVER
        or can(role, "can_alerts_all")
        or can(role, "can_alerts_vehicle")
    )
    fetch_parking = can(role, "can_alerts_all") or can(role, "can_alerts_vehicle")
    # Wide-only fetch, exactly today's deny-set (the legacy _all
    # gate) — a width-aware assigned count is an E-stage question.
    fetch_maintenance = can(role, "can_manage_maintenance")

    # Fetch ALL open tasks (pending / overdue / in_progress) when
    # the role can see maintenance — the count below filters them by
    # urgency, not by stored status.  A pending task that's still
    # 50,000 mi from due isn't "Maintenance due", it's scheduled.
    overview, pending_alerts, all_parked, maint_tasks = await asyncio.gather(
        get_vehicles_for_map(account_id, company=company),
        # LOGICAL alerts (alert_history: one row per truck+problem), NOT
        # delivery receipts.  get_pending_alerts reads
        # alert_acknowledgments, which holds one row per RECIPIENT — three
        # people DM'd about one fault counted as three "pending alerts", so
        # this card and the bell badge reported a number the Alerts board
        # never agreed with.  alert_history is the SSOT (the board reads it);
        # unwindowed, because an unacknowledged alert is open regardless of
        # age.  This is the same call /alerts/pending/count makes, so the
        # card, the badge and the board can't drift apart; only alert_type
        # and vehicle_name are read below.
        tenant_db.get_active_alert_history_for_account(
            account_id,
        ) if fetch_alerts else asyncio.sleep(0, result=[]),
        tenant_db.get_active_parking_events(account_id, attention_only=False) if fetch_parking else asyncio.sleep(0, result=[]),
        tenant_db.get_maintenance_tasks(account_id) if fetch_maintenance else asyncio.sleep(0, result=[]),
    )
    overview = filter_by_allowed_companies(overview, allowed)

    if role == Role.DRIVER:
        from interfaces.api.deps import get_user_vehicle_scope
        scope = await get_user_vehicle_scope(user)
        trucks = await get_user_vehicle_nums(user)
        truck_num = trucks[0] if trucks else None
        my_truck = None
        if scope is not None and not scope.empty:
            my_truck = next(
                (v for v in overview if scope.allows_row(v, name_key="name")),
                None,
            )
        elif trucks:
            allowed = {t.strip().lower() for t in trucks}
            my_truck = next(
                (v for v in overview
                 if v.get("name", "").strip().lower() in allowed),
                None,
            )

        my_alerts = []
        if pending_alerts:
            if scope is not None and not scope.empty:
                my_alerts = [a for a in pending_alerts
                             if scope.allows_row(a, name_key="vehicle_name")]
            elif trucks:
                allowed = {t.strip().lower() for t in trucks}
                my_alerts = [
                    a for a in pending_alerts
                    if (a.get("vehicle_name") or "").strip().lower() in allowed
                ]

        # "vehicles" is the wire key ("fleet" = deprecated alias, same
        # object — remove once no pre-rename SPA bundle is live); see
        # the naming note on the main branch below.
        driver_vehicles = {
            "total": 1 if my_truck else 0, "moving": 0, "idle": 0, "stopped": 0,
        }
        return {
            "role": "driver",
            "truck_num": truck_num,
            "my_truck": {
                "name": my_truck.get("name", truck_num or "—") if my_truck else (truck_num or "—"),
                "status": (
                    {"moving": "Moving", "idle": "Idle", "stopped": "Stopped"}.get(
                        classify_vehicle_status(my_truck), "Unknown"
                    )
                ) if my_truck else "Unknown",
                "speed_mph": round(
                    my_truck.get("location", {}).get("speedMilesPerHour")
                    or my_truck.get("location", {}).get("speed")
                    or 0, 1
                ) if my_truck else 0,
                "fuel_pct": (
                    my_truck.get("fuel", {}).get("value")
                    if isinstance(my_truck.get("fuel"), dict)
                    else my_truck.get("fuel")
                ) if my_truck else None,
                "location": (
                    my_truck.get("location", {}).get("reverseGeo", {}).get("formattedLocation")
                    or my_truck.get("location", {}).get("address")
                    or ""
                ) if my_truck else "",
                "faults": len(
                    my_truck.get("fault_codes", {}).get("j1939", {}).get("diagnosticTroubleCodes", [])
                ) if my_truck else 0,
                "company": my_truck.get("_org", "") if my_truck else "",
            },
            "my_alerts": len(my_alerts),
            "vehicles": driver_vehicles,
            "fleet": driver_vehicles,
        }

    # Per-type buckets: motion states only mean something for tracked
    # units — a registry vehicle with no telematics (most trailers,
    # some trucks) counts as "no_signal", never as "stopped".
    def _blank() -> dict:
        return {"total": 0, "moving": 0, "idle": 0, "stopped": 0, "no_signal": 0}

    trucks, trailers = _blank(), _blank()
    for v in overview:
        bucket = trailers if v.get("vehicle_type") == "trailer" else trucks
        bucket["total"] += 1
        if v.get("_no_telemetry"):
            bucket["no_signal"] += 1
        else:
            bucket[classify_vehicle_status(v)] += 1

    # ROLE-NEUTRAL wire key: "vehicles" (Vehicle is the parent of truck
    # and trailer).  Deliberately NOT "fleet" — in this codebase "fleet"
    # is a ROLE name (fleet.4truck.us, FleetShell, the persona strings),
    # and role-flavored words in the UI ("Fleet Overview" vs "Safety
    # Overview") are GENERATED from the active view, never hardcoded.
    # This block is returned identically to every role; only the fields
    # after it are permission-gated.  Top-level moving/idle/stopped are
    # TRACKED counts (no_signal split out) so "stopped" means
    # engine-off, not "no data".
    vehicles_block = {
        "total": trucks["total"] + trailers["total"],
        "moving": trucks["moving"] + trailers["moving"],
        "idle": trucks["idle"] + trailers["idle"],
        "stopped": trucks["stopped"] + trailers["stopped"],
        "no_signal": trucks["no_signal"] + trailers["no_signal"],
        "trucks": trucks,
        "trailers": trailers,
    }
    result: dict = {
        "role": role,
        "vehicles": vehicles_block,
        # Deprecated alias (same object) — keeps any pre-rename SPA
        # bundle alive during rollout; remove after clients migrate.
        "fleet": vehicles_block,
    }

    if can(role, "can_faults"):
        result["faults"] = sum(
            1 for v in overview
            if v.get("fault_codes", {}).get("j1939", {}).get("diagnosticTroubleCodes")
        )

    if can(role, "can_fuel"):
        result["low_fuel"] = sum(
            1 for v in overview
            if isinstance(v.get("fuel"), dict)
            and v["fuel"].get("value") is not None
            and v["fuel"]["value"] < 20
        )

    if fetch_parking:
        # Strict persona binding: count only alerts whose alert_type
        # belongs to the active view's persona.  Owner / Admin land
        # here too — their persona (owner_admin) has no operational
        # alert types, so ``allowed_types`` is empty and pending_alerts
        # is omitted from the response.  Owner's Overview shows
        # EscalationStatusCard + AccountAlertSummary instead; to do
        # operational triage they switch view to Fleet/Safety/Dispatch
        # via the persona selector (SPA sends X-View-As; backend then
        # returns that role's filtered count).
        from capabilities.alerting import persona_mapping
        allowed_types = persona_mapping.alert_types_for_role(view_role)
        if allowed_types:
            relevant_alerts = [
                a for a in (pending_alerts or [])
                if (a.get("alert_type") or "") in allowed_types
            ]
            result["pending_alerts"] = len(relevant_alerts)
            # How long the WORST thing has been waiting.  A count answers
            # "how much is open" but never "how badly are we doing" — a
            # steady 400 could be 400 fresh alerts or one critical ignored
            # for a fortnight, and only the second is an emergency.  Free
            # here: these rows are already in memory for the count above.
            oldest = min(
                (a.get("first_seen") for a in relevant_alerts
                 if (a.get("severity") or "") == "critical" and a.get("first_seen")),
                default=None,
            )
            if oldest:
                result["oldest_critical_first_seen"] = oldest
        # else: omit pending_alerts entirely so the SPA's
        # ``showWhen: stats.pending_alerts !== undefined`` check hides
        # the card on owner/admin views — they don't triage alerts.

        result["unsafe_parking"] = sum(1 for e in all_parked if e.get("location_class") == "unsafe")
        result["unknown_parking"] = sum(1 for e in all_parked if e.get("location_class") == "unknown")

    if fetch_maintenance:
        # ``maintenance_due`` counts tasks that classify as overdue OR
        # due_soon (within 7 days / 5,000 mi / 100 engine hours of the
        # threshold).  Matches the dashboard's "Due Soon + Overdue"
        # chips so the shell header badge and the Maintenance page
        # tell the same story.  A pending task that's still 50,000 mi
        # away from due doesn't count.
        from features.maintenance.service import account_today, classify_task_urgency
        _today = await account_today(account_id)
        due_now = 0
        for t in (maint_tasks or []):
            urgency = classify_task_urgency(t, today=_today)
            if urgency in ("overdue", "due_soon"):
                due_now += 1
        result["maintenance_due"] = due_now

    if cache_key is not None:
        _stats_cache_put(cache_key, result)
    return result
