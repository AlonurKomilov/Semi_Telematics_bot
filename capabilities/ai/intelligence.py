"""AI intelligence: diagnose, summarise, ask, and agent mode."""

from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace

from capabilities.ai.cache import _cache_key, _cache_get, _cache_put, _snapshot_hash
from capabilities.ai.chat import _chat_histories, _store_history
from capabilities.ai.models import (
    _account_models,
    _user_models,
    get_current_model_name,
    get_model_for_user,
)
from capabilities.ai.generation import (
    generate,
    _capture_usage,
    ASSISTANT_SYSTEM,
    FAULT_DIAGNOSIS_SYSTEM,
    SUMMARY_SYSTEM,
)
from capabilities.ai.tools import (  # noqa: E402,F401
    execute_tool as _execute_tool,
    get_cached_vertex_tools as _get_cached_tools,
    get_anthropic_tools as _get_anthropic_tools,
    get_openai_tools as _get_openai_tools,
    get_tool_schema,
    AI_TOOLS,  # backward compat re-export
)
from capabilities.permissions.roles import (
    TOOL_PERMISSIONS, ACCOUNT_WIDE_TOOLS, VEHICLE_SPECIFIC_TOOLS, SCOPE_AWARE_TOOLS,
)

logger = logging.getLogger("bot.ai")

# Live token/reasoning streaming for the Gemini agent path.  Default OFF:
# when on, the model call switches to generate_content_stream and emits live
# ``thinking`` (reasoning) + ``delta`` (answer) SSE events as they arrive.
# Must be verified against live Vertex before enabling — toggle with
# AI_STREAM_TOKENS=1.  The non-streaming path is otherwise unchanged.
_STREAM_TOKENS = os.getenv("AI_STREAM_TOKENS", "").strip().lower() in ("1", "true", "yes", "on")

# Human-readable labels for tool calls sent to streaming clients
_TOOL_LABELS: dict[str, str] = {
    "get_vehicle_faults":     "Checking fault codes",
    "get_vehicle_detail":     "Reading vehicle info",
    "get_vehicle_location":   "Getting location",
    "get_rolling_stopped":    "Checking rolling status",
    "get_low_fuel_vehicles":  "Scanning fuel levels",
    "get_vehicle_fuel_costs": "Analyzing fuel costs",
    "get_fuel_cost_summary":  "Summarizing fuel costs",
    "get_vehicle_health":     "Checking vehicle health",
    "get_vehicle_events":     "Looking up events",
    "get_events_summary":     "Summarizing events",
    "get_vehicle_maintenance":"Checking maintenance",
    "get_maintenance_summary":"Reviewing maintenance",
    "get_driver_efficiency":  "Analyzing driver efficiency",
    "get_efficiency_summary": "Summarizing efficiency",
    "get_driver_scorecard":   "Getting scorecard",
    "get_drivers_list":       "Listing drivers",
    "check_vehicle_camera":   "Checking camera",
    "get_geofences":          "Checking geofences",
    "get_weather":            "Getting weather",
    "get_vehicle_odometer":   "Reading odometer",
    "search_vehicles":        "Searching vehicles",
    "search_knowledge_base":  "Searching knowledge base",
    "get_account_stats":      "Getting account stats",
    "get_parked_vehicles":    "Checking parked vehicles",
    "get_undriven_vehicles":  "Checking undriven vehicles",
    "get_driver_hos_status":  "Reading driver hours",
    "get_alert_history":      "Reviewing alert history",
    "get_recent_work_orders": "Looking up shop visits",
    "get_recent_inspections": "Reviewing inspections",
    "get_vehicle_history":    "Reading vehicle history",
    "get_driver_applications": "Checking the applicant pipeline",
}


# ── Shared context builder ───────────────────────────────────────

async def build_context(account_id: int,
                        vehicle_num: str | None = None,
                        vehicle_nums: list[str] | None = None) -> dict:
    """Build a compact data snapshot for AI context.

    Reused by both the Telegram bot and the web API.
    If vehicle_num/vehicle_nums is provided (Driver role), only those trucks are included.
    """
    from infra.services import get_tenant_db

    # Normalize to a set of lowercase truck names for filtering
    _vehicle_set: set[str] | None = None
    if vehicle_nums:
        _vehicle_set = {t.lower() for t in vehicle_nums if t}
    elif vehicle_num:
        _vehicle_set = {vehicle_num.lower()}

    # Warehouse-first reads via service layer (falls back to live Samsara on
    # cold-start or when WAREHOUSE_READS_ENABLED=0).
    from features.vehicles.service import get_vehicles_overview as _svc_vehicles_overview
    from capabilities.warehouse.telemetry.service import get_vehicle_health as _svc_vehicle_health
    from features.events.service import get_events as _svc_get_events
    snapshot: dict = {}

    try:
        vehicles = await _svc_vehicles_overview(account_id)
        if _vehicle_set:
            vehicles = [
                v for v in vehicles
                if v.get("name", "").lower() in _vehicle_set
            ]

        snapshot["total_vehicles"] = len(vehicles)
        snapshot["vehicles"] = []
        for v in vehicles[:50]:
            entry: dict = {
                "name": v.get("name", "?"),
                "company": v.get("_org", "?"),
            }
            loc = v.get("location", {})
            if loc:
                entry["city"] = loc.get("reverseGeo", {}).get(
                    "formattedLocation", ""
                )
            fuel = v.get("fuel", {})
            if fuel.get("value") is not None:
                entry["fuel_pct"] = fuel["value"]
            dtcs = v.get("_dtcs", [])
            if dtcs:
                entry["fault_count"] = len(dtcs)
                entry["faults"] = [
                    {
                        "spn": d.get("spnId"),
                        "description": d.get("spnDescription", ""),
                        "severity": d.get("fmiDescription", ""),
                    }
                    for d in dtcs[:5]
                ]
            lights = v.get("_lights", {})
            if any(lights.get(k) for k in
                   ("stopIsOn", "protectIsOn", "emissionsIsOn", "warningIsOn")):
                entry["check_engine_lights"] = {
                    k: lv for k, lv in lights.items() if lv
                }
            snapshot["vehicles"].append(entry)
    except Exception as e:
        logger.error(f"AI vehicle snapshot failed: {e}")
        snapshot["error"] = str(e)

    # Health data
    try:
        health = await _svc_vehicle_health(account_id)
        if _vehicle_set:
            health = [
                v for v in health
                if v.get("name", "").lower() in _vehicle_set
            ]
        alerts_summary = []
        for v in health:
            h_alerts = v.get("_health_alerts", [])
            if h_alerts:
                alerts_summary.append({
                    "vehicle": v.get("name", "?"),
                    "alerts": h_alerts,
                    "battery_v": v.get("_health", {}).get("battery_v"),
                    "coolant_c": v.get("_health", {}).get("coolant_c"),
                    "oil_psi": v.get("_health", {}).get("oil_psi"),
                    "def_pct": v.get("_health", {}).get("def_pct"),
                })
        if alerts_summary:
            snapshot["health_alerts"] = alerts_summary
    except Exception as e:
        logger.debug(f"AI health snapshot skipped: {e}")

    # Counts
    faulted = [v for v in snapshot.get("vehicles", []) if v.get("fault_count")]
    low_fuel = [v for v in snapshot.get("vehicles", [])
                if v.get("fuel_pct") is not None and v["fuel_pct"] <= 20]
    snapshot["faulted_count"] = len(faulted)
    snapshot["low_fuel_count"] = len(low_fuel)

    # Fetch health, events, maintenance, fuel in parallel
    import asyncio

    async def _fetch_health():
        try:
            health = await _svc_vehicle_health(account_id)
            if _vehicle_set:
                health = [
                    v for v in health
                    if v.get("name", "").lower() in _vehicle_set
                ]
            alerts_summary = []
            for v in health:
                h_alerts = v.get("_health_alerts", [])
                if h_alerts:
                    alerts_summary.append({
                        "vehicle": v.get("name", "?"),
                        "alerts": h_alerts,
                        "battery_v": v.get("_health", {}).get("battery_v"),
                        "coolant_c": v.get("_health", {}).get("coolant_c"),
                        "oil_psi": v.get("_health", {}).get("oil_psi"),
                        "def_pct": v.get("_health", {}).get("def_pct"),
                    })
            if alerts_summary:
                snapshot["health_alerts"] = alerts_summary
        except Exception as e:
            logger.debug(f"AI health snapshot skipped: {e}")

    async def _fetch_events():
        try:
            events = await _svc_get_events(account_id, days=7)
            if _vehicle_set:
                events = [
                    e for e in events
                    if e.get("vehicle_name", "").lower() in _vehicle_set
                ]
            if events:
                evt_by_type: dict[str, int] = {}
                by_truck: dict[str, dict[str, int]] = {}
                for ev in events:
                    etype = ev.get("event_name", "Unknown")
                    evt_by_type[etype] = evt_by_type.get(etype, 0) + 1
                    vname = ev.get("vehicle_name", "?")
                    if vname not in by_truck:
                        by_truck[vname] = {}
                    by_truck[vname][etype] = by_truck[vname].get(etype, 0) + 1
                snapshot["recent_events"] = {
                    "period_days": 7,
                    "total": len(events),
                    "by_type": evt_by_type,
                    "by_truck": [
                        {"vehicle": t, "total": sum(types.values()), "types": types}
                        for t, types in sorted(
                            by_truck.items(),
                            key=lambda x: sum(x[1].values()),
                            reverse=True,
                        )[:30]
                    ],
                }
        except Exception as e:
            logger.debug(f"AI events snapshot skipped: {e}")

    async def _fetch_maintenance():
        try:
            tenant = await get_tenant_db(account_id)
            tasks = await tenant.get_maintenance_tasks(account_id)
            if _vehicle_set:
                tasks = [t for t in tasks if t.get("vehicle_name", "").lower() in _vehicle_set]
            # DERIVED urgency (date / mileage / engine-hours), same as the
            # dashboard chips and the maintenance AI tools.  Counting the
            # stored ``status`` column here made the model answer "0 overdue"
            # straight from the snapshot (it's told to skip tools when the
            # snapshot suffices) while the page showed a truck past its due
            # odometer as overdue.
            from features.maintenance.ai_tool import _bucket_open_tasks, _task_row
            from features.maintenance.service import apply_live_readings
            # Live odometer / engine-hours merge before classifying —
            # stored readings can be stranded stale (alerted_at filter).
            await apply_live_readings(tenant, account_id, tasks)
            overdue, due_soon, pending = _bucket_open_tasks(tasks)
            if overdue or due_soon or pending:
                ordered = (
                    [(t, "overdue") for t in overdue]
                    + [(t, "due_soon") for t in due_soon]
                    + [(t, "pending") for t in pending]
                )
                snapshot["maintenance"] = {
                    "pending": len(pending),
                    "due_soon": len(due_soon),
                    "overdue": len(overdue),
                    "tasks": [_task_row(t, u) for t, u in ordered[:10]],
                }
        except Exception as e:
            logger.debug(f"AI maintenance snapshot skipped: {e}")

    async def _fetch_fuel_costs():
        try:
            tenant_fuel = await get_tenant_db(account_id)
            fuel_summary = await tenant_fuel.get_fuel_summary(account_id)
            if _vehicle_set:
                fuel_summary = [
                    s for s in fuel_summary
                    if s.get("vehicle_name", "").lower() in _vehicle_set
                ]
            if fuel_summary:
                items = []
                for s in fuel_summary[:15]:
                    first_odo = s.get("first_odo") or 0
                    last_odo = s.get("last_odo") or 0
                    miles = last_odo - first_odo if last_odo > first_odo else 0
                    total_cost = s.get("total_cost") or 0
                    items.append({
                        "vehicle": s.get("vehicle_name", "?"),
                        "total_gallons": round(s.get("total_gallons") or 0, 1),
                        "total_cost": round(total_cost, 2),
                        "avg_price": round(s.get("avg_price") or 0, 3),
                        "cost_per_mile": round(total_cost / miles, 3) if miles > 0 else None,
                    })
                snapshot["fuel_costs"] = items
        except Exception as e:
            logger.debug(f"AI fuel cost snapshot skipped: {e}")

    await asyncio.gather(
        _fetch_health(), _fetch_events(),
        _fetch_maintenance(), _fetch_fuel_costs(),
    )

    return snapshot


async def diagnose_faults(vehicle_name: str,
                          dtcs: list[dict],
                          lights: dict | None = None,
                          account_id: int | None = None,
                          language: str = "en",
                          user_context: dict | None = None,
                          ) -> tuple[str, dict | None]:
    """AI-powered fault code diagnosis for a specific vehicle.

    Returns ``(diagnosis_text, usage)``.
    """
    context = {
        "vehicle": vehicle_name,
        "check_engine_lights": lights or {},
        "active_faults": [
            {
                "spn": dtc.get("spnId"),
                "fmi": dtc.get("fmiId"),
                "description": dtc.get("spnDescription", "Unknown"),
                "severity": dtc.get("fmiDescription", "Unknown"),
                "source": dtc.get("sourceAddressName", "Unknown"),
                "count": dtc.get("count", 1),
            }
            for dtc in dtcs[:10]
        ],
    }
    prompt = (
        f"Diagnose the active faults on Truck #{vehicle_name}. "
        f"There are {len(dtcs)} active fault code(s)."
    )
    return await generate(prompt, system=FAULT_DIAGNOSIS_SYSTEM,
                          context_data=context, account_id=account_id,
                          language=language, user_context=user_context,
                          action="diagnosis")


async def generate_summary(vehicle_data: dict,
                           account_id: int | None = None,
                           language: str = "en",
                           user_context: dict | None = None,
                           ) -> tuple[str, dict | None]:
    """Generate an AI status briefing tailored to the caller's role.

    The focus areas are NOT hardcoded per role: they resolve from the role's
    effective per-account permissions via ``briefing_focus_for_account``
    (the Permissions-matrix SSOT).  A dispatcher gets movement/availability,
    safety gets events/coaching, a recruiter gets the hiring pipeline — and a
    future feature added to ``BRIEFING_TOPICS`` reaches every role that holds
    its permission with no changes here.  Falls back to a generic operations
    briefing when the role can't be resolved.

    Returns ``(summary_text, usage)``.
    """
    role = (user_context or {}).get("role")
    topics: list[str] = []
    if role and account_id:
        try:
            from capabilities.permissions.roles import briefing_focus_for_account
            topics = await briefing_focus_for_account(role, account_id)
        except Exception:
            logger.debug("briefing focus resolution failed; generic briefing")
            topics = []
    if topics:
        prompt = (
            f"Generate a morning status briefing for this user (role: {role}) "
            f"from this data. Their work areas: {'; '.join(topics)}. "
            "Cover ONLY these areas, leading with whatever most needs "
            "attention and weighting the areas most central to their role. "
            "Skip any area with no data in the snapshot rather than saying "
            "it is unavailable."
        )
    else:
        prompt = "Generate a morning operations status briefing from this data."
    return await generate(prompt, system=SUMMARY_SYSTEM,
                          context_data=vehicle_data, account_id=account_id,
                          language=language, user_context=user_context,
                          action="summary")


async def ask_ai(question: str, vehicle_context: dict,
                 user_id: int | None = None,
                 account_id: int | None = None,
                 language: str = "en",
                 user_context: dict | None = None,
                 ) -> tuple[str, dict | None]:
    """Answer a natural-language question using AI.

    Returns ``(answer_text, usage)``.
    """
    return await generate(question, system=ASSISTANT_SYSTEM,
                          context_data=vehicle_context, user_id=user_id,
                          account_id=account_id, language=language,
                          user_context=user_context,
                          action="question")


# ── Function-Calling Agent ───────────────────────────────────────


def _render_page_context(user_context: dict | None) -> str:
    """Copilot page context → a prompt block, or "" when none.

    Describes what the user is looking at RIGHT NOW (feature / focused
    entity / filters / selection) so "this truck", "these rows", "why is
    it overdue" resolve to what's on their screen.  A HINT for
    interpretation ONLY — the tools the model may call and the vehicles
    it may read are enforced server-side from the JWT, unaffected by
    anything in here (same trust boundary as X-View-As persona preview).
    """
    if not user_context:
        return ""
    pc = user_context.get("page_context")
    if not isinstance(pc, dict) or not pc.get("feature"):
        return ""
    lines = ["The user is currently viewing this screen:",
             f"- Page: {pc.get('label') or pc['feature']}"]
    focus = pc.get("focus")
    if isinstance(focus, dict) and focus.get("label"):
        kind = focus.get("kind")
        lines.append(f"- Focused on: {focus['label']}{f' ({kind})' if kind else ''}")
    filters = pc.get("filters")
    if isinstance(filters, dict):
        active = {k: v for k, v in filters.items() if v not in (None, "", "all", [])}
        if active:
            lines.append(f"- Active filters: {json.dumps(active, default=str)[:400]}")
    sel = pc.get("selectedIds")
    if isinstance(sel, list) and sel:
        lines.append(f"- Selected: {len(sel)} item(s)")
    lines.append(
        'When the user says "this", "these", "here", or asks without '
        "naming an entity, assume they mean what's shown above."
    )
    return "\n".join(lines) + "\n\n"


def _build_agent_user_prompt(
    question: str,
    vehicle_context: dict | None,
    user_context: dict | None,
    history: list | None,
) -> str:
    """Build the user-side prompt body (profile + snapshot + history + question).

    Shared by the Gemini and Anthropic agent paths.  The system prompt
    (ASSISTANT_SYSTEM + language hint) is built separately so the
    Anthropic path can put it in the dedicated ``system`` field.
    """
    parts: list[str] = []
    if user_context:
        uc = user_context
        profile_lines = ["User profile:"]
        if uc.get("name"):
            profile_lines.append(f"- Name: {uc['name']}")
        if uc.get("role"):
            profile_lines.append(f"- Role: {uc['role']}")
        if uc.get("vehicle_nums") and len(uc["vehicle_nums"]) > 0:
            profile_lines.append(f"- Assigned trucks: {', '.join(uc['vehicle_nums'])}")
        elif uc.get("vehicle_num"):
            profile_lines.append(f"- Assigned vehicle: {uc['vehicle_num']}")
        if uc.get("timezone"):
            profile_lines.append(f"- Timezone: {uc['timezone']}")
        # Anchor "now" — kills the date-guessing spirals on date-relative
        # questions (see current_datetime_line's docstring).
        from capabilities.ai.usage import current_datetime_line
        profile_lines.append(current_datetime_line(uc.get("timezone")))
        if uc.get("role") == "driver" and (uc.get("vehicle_nums") or uc.get("vehicle_num")):
            trucks = uc.get("vehicle_nums") or [uc["vehicle_num"]]
            trucks_str = ", ".join(trucks)
            profile_lines.append(
                f"\nCRITICAL DRIVER RESTRICTION:"
                f"\nYou are a driver assigned to truck(s): {trucks_str} ONLY."
                f"\n- You MUST ONLY query data for your assigned trucks."
                f"\n- NEVER look up, mention, or reveal data about any other truck."
                f"\n- When calling any tool, always use vehicle_name for one of your assigned trucks."
                f"\n- Do NOT call account-wide tools (whole-account summaries, all trucks, etc.)."
                f"\n- If the user asks about another truck or account-wide totals, politely decline."
            )
        parts.append("\n".join(profile_lines) + "\n\n")
        parts.append(_render_page_context(user_context))

    if vehicle_context:
        data_str = json.dumps(vehicle_context, separators=(',', ':'), default=str)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Vehicle snapshot:\n```\n{data_str}\n```\n\n")

    parts.append(
        "You have access to tools that can fetch live data from Samsara. "
        "Use them when the user asks about specific trucks, faults, fuel, "
        "driver efficiency, scorecards, safety events, maintenance, "
        "fuel costs, rolling/stopped status, or cameras. "
        "For safety events, always state the time period checked. "
        "For camera checks, only check one truck at a time — for account-wide "
        "camera checks, direct the user to the Camera Check menu. "
        "If the snapshot already has enough info, answer directly without "
        "calling tools.\n\n"
        "IMPORTANT: If a tool returns an 'error' field in its result, you "
        "MUST report that error to the user honestly. Never hide tool "
        "failures or pretend the data is fine. Tell the user something "
        "like 'I tried to check X but got an error: [message]'.\n\n"
    )

    if history:
        parts.append("Previous conversation:\n")
        for entry in history:
            parts.append(f"{entry['role']}: {entry['text']}\n")
        parts.append("\n")

    parts.append(f"User question: {question}")
    return "".join(parts)


def _scoped_vehicle_set(user_context: dict | None, user_role: str | None) -> list | None:
    """The caller's effective allowed-vehicle list, or ``None`` if unrestricted.

    Reads the resolved ``scoped_vehicle_nums`` (set at the AI entry point from
    the Vehicle Access SSOT).  Back-compat: when that key is absent we derive a
    driver's set from their assigned trucks, so callers predating the scope
    plumbing keep driver isolation.
    """
    if user_context is None:
        return None
    if "scoped_vehicle_nums" in user_context:
        return user_context["scoped_vehicle_nums"]
    if user_role == "driver":
        trucks = user_context.get("vehicle_nums") or []
        if not trucks and user_context.get("vehicle_num"):
            trucks = [user_context["vehicle_num"]]
        return [t for t in trucks if t]
    return None


def _effective_scoped_flag(user_context: dict | None, user_role: str | None) -> bool:
    """True if the caller is vehicle/company-restricted — used to drop
    account-wide tools from what the model is advertised.  Mirrors the gate's
    scope determination so advertisement and gate agree.
    """
    return _scoped_vehicle_set(user_context, user_role) is not None


async def _check_tool_permission(
    tool_name: str,
    tool_args: dict,
    user_role: str | None,
    user_context: dict | None,
    account_id: int | None = None,
) -> dict | None:
    """Server-side guard: enforce role permissions on tool execution.

    Resolves permissions **account-aware** via ``get_account_permissions``
    — the same source of truth the API, dashboard, and bot enforce — so a
    per-account override set in the Role Permissions matrix, or a disabled
    module, is honored here too rather than silently bypassed by the
    hardcoded role defaults.  Falls back to role defaults only when
    ``account_id`` is unknown (e.g. an unauthenticated context).

    Returns ``None`` if the call is allowed.  Returns a ``{"error": …}``
    dict if blocked — caller feeds that back to the model so it can
    explain the refusal to the user.
    """
    if not user_role:
        return None
    # Write actions are suppressed in two cases, both checked at PROPOSE
    # time so the model never offers a dead-end action:
    #   • preview_active — an owner/admin is previewing another role
    #     (X-View-As); preview is a read-only lens (fable-advisor).
    #   • suppress_writes — the calling SURFACE has no approve UI / no
    #     proposal persistence (the non-streaming /ai/chat path used by the
    #     Telegram miniapp).  A proposal there could never be approved.
    # The execute endpoint re-checks on the real role regardless; blocking
    # the PROPOSE here keeps the preview faithful and the audit clean.
    if user_context and (user_context.get("preview_active")
                         or user_context.get("suppress_writes")):
        _schema = get_tool_schema(tool_name)
        if _schema and _schema.get("writes"):
            if user_context.get("preview_active"):
                return {"error": (
                    "Write actions are disabled while previewing another role. "
                    "Exit the preview to make changes."
                )}
            return {"error": (
                "Write actions aren't available on this surface — open the "
                "dashboard assistant to create or change anything."
            )}
    req_perms = TOOL_PERMISSIONS.get(tool_name)
    if req_perms is not None:
        try:
            from adapters.storage import Role
            role = Role(user_role)
            if account_id is not None:
                from capabilities.permissions.roles import get_account_permissions
                perms = await get_account_permissions(role, int(account_id))
            else:
                from capabilities.permissions.roles import get_permissions
                perms = get_permissions(role)
            if not any(getattr(perms, p, False) for p in req_perms):
                return {"error": f"Access denied: your role ({user_role}) cannot use {tool_name}."}
        except (ValueError, KeyError, ImportError) as e:
            logger.debug("Tool dispatch error for %s: %s", tool_name, e)
    # Vehicle-Access isolation (Account → Company → Vehicle SSOT).  The AI
    # entry point resolves the caller's effective scope into
    # ``scoped_vehicle_nums``: ``None`` = unrestricted; a list = the only
    # vehicles allowed (``[]`` = none).  This one rule covers drivers,
    # vehicle-scoped, and company-scoped users alike.  Back-compat: when the
    # key is absent we fall back to a driver's assigned trucks, so callers
    # that predate the scope plumbing keep their driver isolation.
    scoped = _scoped_vehicle_set(user_context, user_role)

    if scoped is not None:
        allowed_set = {t.strip().lower() for t in scoped if t}
        names = ", ".join(scoped) if scoped else "(none assigned)"
        if tool_name in VEHICLE_SPECIFIC_TOOLS:
            requested = (tool_args.get("vehicle_name") or "").strip().lower()
            if not requested:
                # Tools with an optional vehicle_name run account-wide when it
                # is omitted — a scoped user must always name an allowed one.
                return {
                    "error": (
                        f"Access denied: you can only query your assigned"
                        f" vehicle(s) ({names}). Call {tool_name} again with"
                        f" vehicle_name set to one of them."
                    ),
                }
            if requested not in allowed_set:
                return {
                    "error": (
                        f"Access denied: you can only query your assigned"
                        f" vehicle(s) ({names}),"
                        f" not '{tool_args.get('vehicle_name')}'."
                    ),
                }
        if tool_name in ACCOUNT_WIDE_TOOLS and tool_name not in SCOPE_AWARE_TOOLS:
            # Scope-aware account-wide tools are allowed — they filter their
            # own results to the caller's vehicles (scope injected at execute).
            # Tools not yet scope-aware stay blocked (the safe default).
            return {
                "error": (
                    f"Access denied: {tool_name} returns account-wide data"
                    f" outside your access scope."
                ),
            }
        # Fail-closed backstop for WRITE tools: a write must carry an explicit
        # scope shape — either a vehicle_name param (VEHICLE_SPECIFIC_TOOLS) or
        # id-filtering scope-awareness (SCOPE_AWARE_TOOLS).  A writes:True tool
        # in neither is an un-scoped write; deny it for a restricted caller so a
        # forgotten classification degrades to "blocked", never "account-wide
        # write".  (The guard test in tests/test_ai_write_tool_scope.py stops
        # this reaching prod, but the runtime deny is the belt to its braces.)
        _schema = get_tool_schema(tool_name)
        if (_schema and _schema.get("writes")
                and tool_name not in VEHICLE_SPECIFIC_TOOLS
                and tool_name not in SCOPE_AWARE_TOOLS):
            return {
                "error": (
                    f"Access denied: {tool_name} is a write action not scoped"
                    f" to your vehicle access."
                ),
            }
    return None


# Default agentic tool-call rounds per provider path: 1 initial model call plus
# (N-1) tool re-calls.  These were hardcoded as ``range(4)`` / ``range(3)``;
# lifting them here lets a model opt into a deeper loop via the registry
# (``"max_tool_rounds": N``) — the knob a future reasoning/autonomous tier needs
# to run longer without editing this file.  No model sets it today, so the loop
# depth is unchanged.
_DEFAULT_TOOL_ROUNDS_ANTHROPIC = 4
_DEFAULT_TOOL_ROUNDS_GEMINI = 3


def _resolve_tool_rounds(model_info: dict, default: int) -> int:
    """Tool-call loop depth for a model: its registry ``max_tool_rounds`` or the
    provider default.  Floored at 1 so a misconfigured value can never disable
    the initial model call; a non-int value falls back to the default."""
    try:
        return max(1, int(model_info.get("max_tool_rounds", default)))
    except (TypeError, ValueError):
        return default


async def _run_anthropic_agent(
    question: str,
    vehicle_context: dict,
    samsara_client,
    model_name: str,
    model_info: dict,
    user_id: int | None,
    account_id: int | None,
    db,
    language: str,
    user_context: dict | None,
    event_callback,
) -> dict:
    """Anthropic Claude function-calling loop (Vertex AI rawPredict).

    Claude on Vertex AI returns tool calls as content blocks of type
    ``tool_use``; we execute the tool, append a ``tool_result`` user
    message, and re-call up to 3 rounds.  Mirrors the role/permission
    enforcement of the Gemini path.
    """
    import asyncio
    import os
    import requests
    from google.auth.transport.requests import Request

    from capabilities.ai.registry import (
        _anthropic_url,
        _get_credentials,
        model_temperature,
    )

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        text, usage = await ask_ai(
            question, vehicle_context, user_id=user_id,
            account_id=account_id, language=language,
            user_context=user_context,
        )
        return {"text": text, "tool_results": [], "usage": usage}
    creds.refresh(Request())

    location = model_info.get("locations", ["global"])[0]
    anthropic_model_id = model_info["anthropic_model_id"]
    max_tokens = min(model_info.get("max_output_tokens", 4096), 8192)
    max_tool_rounds = _resolve_tool_rounds(model_info, _DEFAULT_TOOL_ROUNDS_ANTHROPIC)
    _agent_temperature = model_temperature(model_info)

    # Cache + history lookups mirror the Gemini path.
    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(vehicle_context) if not has_history else ""
    ck = _cache_key(
        question, snap_h, model_name,
        account_id=account_id or 0,
        user_id=user_id or 0,
        language=language,
    ) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            if user_id is not None:
                _store_history(user_id, question, cached, account_id=account_id or 0)
            return {"text": cached, "tool_results": [], "usage": None}

    user_role = user_context.get("role") if user_context else None
    tools = await _get_anthropic_tools(
        role=user_role, account_id=account_id,
        scoped=_effective_scoped_flag(user_context, user_role),
    )

    system_prompt = ASSISTANT_SYSTEM
    if language and language != "en":
        from capabilities.localization.i18n import LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(language, language)
        system_prompt += (
            f"\n\nIMPORTANT: You MUST respond in {lang_name}. "
            f"All your output text must be in {lang_name}."
        )

    history = _chat_histories.get((user_id or 0, account_id or 0)) if user_id else None
    user_prompt = _build_agent_user_prompt(question, vehicle_context, user_context, history)

    url = _anthropic_url(location, project, anthropic_model_id)
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    tool_results: list[dict] = []
    reasoning_chunks: list[str] = []
    usage_total = {"prompt_tokens": 0, "reply_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
    # Extended thinking (registry-declared) — makes a Thinking-tier
    # Claude actually think.  Tool rounds are compatible because the
    # assistant message is replayed verbatim below, thinking blocks
    # included (the API requires them back unmodified).
    _thinking_budget = model_info.get("anthropic_thinking_budget")

    # Per-attempt telemetry — one ai_usage row per Claude rawPredict
    # call (initial + each tool_use re-call) so the router sees the
    # Anthropic-side latency / 429 / quota signal at the same resolution
    # as the Gemini path.  prompt_category is classified once per turn.
    from capabilities.ai.usage import (
        record_call_attempt as _record_call,
        classify_error as _classify_err,
        classify_prompt as _classify_prompt,
    )
    import time as _t
    _prompt_category = _classify_prompt(question)

    def _post(body: dict) -> dict:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    final_text = ""
    for _round in range(max_tool_rounds):  # 1 initial + (max_tool_rounds-1) tool-use rounds
        from capabilities.ai.generation import _apply_anthropic_thinking
        body = _apply_anthropic_thinking({
            "anthropic_version": "vertex-2023-10-16",
            "system": system_prompt,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": _agent_temperature,
            "top_p": 0.8,
        }, _thinking_budget)
        _started = _t.monotonic()
        try:
            data = await asyncio.to_thread(_post, body)
        except Exception as e:
            latency_ms = int((_t.monotonic() - _started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=user_role, action="question",
                model=model_name, latency_ms=latency_ms,
                error_type=_classify_err(e),
                usage=None,
                prompt_category=_prompt_category,
            )
            logger.warning("Anthropic agent call failed, falling back: %s", e)
            text, usage = await ask_ai(
                question, vehicle_context, user_id=user_id,
                account_id=account_id, language=language,
                user_context=user_context,
            )
            return {"text": text, "tool_results": tool_results, "usage": usage}

        latency_ms = int((_t.monotonic() - _started) * 1000)
        u = data.get("usage", {})
        usage_total["prompt_tokens"] += u.get("input_tokens", 0) or 0
        usage_total["reply_tokens"] += u.get("output_tokens", 0) or 0
        usage_total["total_tokens"] += (u.get("input_tokens", 0) or 0) + (u.get("output_tokens", 0) or 0)

        # Record the success row per attempt — usage carries this
        # round's tokens only, not the cumulative ``usage_total`` (the
        # router scores per attempt, not per turn).
        per_round_usage = {
            "prompt_tokens": u.get("input_tokens", 0) or 0,
            "reply_tokens": u.get("output_tokens", 0) or 0,
            "thinking_tokens": 0,
            "total_tokens": (u.get("input_tokens", 0) or 0)
                            + (u.get("output_tokens", 0) or 0),
        }
        await _record_call(
            account_id=account_id, user_id=user_id,
            role=user_role, action="question",
            model=model_name, latency_ms=latency_ms,
            error_type="ok",
            usage=per_round_usage,
            prompt_category=_prompt_category,
        )

        content_blocks = data.get("content", []) or []
        # Collect text + tool_use + thinking blocks
        text_chunks: list[str] = []
        tool_use_blocks: list[dict] = []
        for block in content_blocks:
            btype = block.get("type")
            if btype == "text":
                text_chunks.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_use_blocks.append(block)
            elif btype == "thinking":
                chunk = block.get("thinking", "")
                if chunk:
                    reasoning_chunks.append(chunk)
                    # Live-stream the reasoning into the chat's
                    # collapsible panel, same as the Gemini path.
                    if event_callback is not None:
                        try:
                            await event_callback(
                                {"type": "thinking", "text": chunk},
                            )
                        except Exception:
                            pass

        stop_reason = data.get("stop_reason")
        # If no tool calls, we're done.
        if not tool_use_blocks or stop_reason != "tool_use":
            final_text = "".join(text_chunks).strip()
            break

        # Append the assistant message (text + tool_use blocks) verbatim,
        # then a user message with one tool_result per tool_use.
        messages.append({"role": "assistant", "content": content_blocks})
        result_blocks: list[dict] = []
        for tu in tool_use_blocks:
            tool_name = tu.get("name", "")
            tool_args = tu.get("input", {}) or {}
            tu_id = tu.get("id", "")
            logger.info("AI agent (anthropic) calling tool: %s(%s)", tool_name, tool_args)

            blocked = await _check_tool_permission(
                tool_name, tool_args, user_role, user_context, account_id,
            )
            if blocked is not None:
                tool_results.append({"tool": tool_name, "args": tool_args, "data": blocked})
                result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": json.dumps(blocked, default=str),
                })
                continue

            if event_callback is not None:
                try:
                    await event_callback({
                        "type": "tool",
                        "name": tool_name,
                        "label": _TOOL_LABELS.get(tool_name, tool_name),
                    })
                except Exception:
                    pass
            try:
                result = await _execute_tool(
                    tool_name, tool_args, samsara_client,
                    account_id=account_id, db=db,
                    scope_vehicles=_scoped_vehicle_set(user_context, user_role),
                )
            except Exception as e:
                result = {"error": f"Tool execution failed: {e}"}
            tool_results.append({"tool": tool_name, "args": tool_args, "data": result})
            result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tu_id,
                "content": json.dumps(result, default=str),
            })
        messages.append({"role": "user", "content": result_blocks})
        # Loop and call again with updated messages.

    if not final_text:
        final_text = (
            "I couldn't generate a response for that question. "
            "Please try rephrasing it."
        )

    usage_out = usage_total if any(usage_total.values()) else None
    if user_id is not None:
        _store_history(user_id, question, final_text, account_id=account_id or 0)
    if ck and not has_history:
        _cache_put(ck, final_text)
    return {
        "text": final_text, "tool_results": tool_results, "usage": usage_out,
        "reasoning": "\n\n".join(reasoning_chunks).strip(),
    }


_DEFAULT_TOOL_ROUNDS_OPENAI = 4


def _clean_tool_arguments(args: str) -> str:
    """Reduce streamed tool-call ``arguments`` to valid JSON.

    R1-style models leak raw special tokens into the streamed arguments
    tail (``{}\\n```<｜tool▁call▁end｜>…``) — the non-stream endpoint
    trims these, the stream does not.  Replaying them 400s the next
    round ("Expected a valid JSON object") and breaks execution-side
    parsing.  Strategy: if the string already parses, keep it; else
    take the first balanced ``{…}`` object (string-aware scan); else
    ``{}``.
    """
    s = (args or "").strip()
    if not s:
        return "{}"
    try:
        json.loads(s)
        return s
    except (TypeError, ValueError):
        pass
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    cand = s[start:i + 1]
                    try:
                        json.loads(cand)
                        return cand
                    except (TypeError, ValueError):
                        start = None
    return "{}"


def _consume_openai_stream(lines, on_thinking=None) -> tuple[dict, dict | None]:
    """Assemble a chat-completions message from an SSE stream.

    Returns ``(message, usage)`` in the same shape a non-streaming
    response's ``choices[0].message`` / ``usage`` carry, so the caller's
    parsing is identical either way.  ``on_thinking(text)`` fires live
    for each reasoning fragment — both ``delta.reasoning_content``
    (DeepSeek V3-style separated field) and content deltas inside an
    R1-style inline ``<think>…</think>`` head, so the chat's live
    timeline shows the model's actual thoughts while it works instead
    of a canned phrase for 30+ seconds.

    Tool-call deltas arrive fragmented (arguments split across chunks,
    keyed by ``index``) — merged here.  The final text is re-parsed by
    ``_parse_openai_compat_reply`` afterwards, so the live ``<think>``
    routing is display-only and needs no perfect tag-boundary handling.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    usage = None
    # Inline-<think> router: decided on the first content delta.
    #   None → undecided, "think" → routing content to thinking,
    #   "answer" → content is answer text (not emitted live).
    mode: str | None = None

    for raw in lines:
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else raw
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        for ch in chunk.get("choices") or []:
            d = ch.get("delta") or {}
            rc = d.get("reasoning_content")
            if rc:
                reasoning_parts.append(rc)
                if on_thinking:
                    on_thinking(rc)
            c = d.get("content")
            if c:
                content_parts.append(c)
                if mode is None:
                    mode = "think" if c.lstrip().startswith("<think") else "answer"
                if mode == "think":
                    if on_thinking:
                        visible = c.replace("<think>", "").replace("</think>", "")
                        if visible:
                            on_thinking(visible)
                    if "</think>" in c:
                        mode = "answer"
            for tc in d.get("tool_calls") or []:
                idx = tc.get("index") or 0
                slot = tool_calls.setdefault(idx, {
                    "id": "", "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]

    message: dict = {
        "role": "assistant",
        "content": "".join(content_parts),
        "reasoning_content": "".join(reasoning_parts),
    }
    if tool_calls:
        calls = [tool_calls[i] for i in sorted(tool_calls)]
        for tc in calls:
            tc["function"]["arguments"] = _clean_tool_arguments(
                tc["function"]["arguments"],
            )
        message["tool_calls"] = calls
    return message, usage


async def _run_openai_compat_agent(
    question: str,
    vehicle_context: dict,
    samsara_client,
    model_name: str,
    model_info: dict,
    user_id: int | None,
    account_id: int | None,
    db,
    language: str,
    user_context: dict | None,
    event_callback,
) -> dict:
    """Function-calling loop for OpenAI-compat MaaS models (DeepSeek,
    Qwen, Kimi, Grok, gpt-oss) — makes the Reasoning tier a first-class
    agent instead of a snapshot-only chat.

    Chat-completions shape: tool calls arrive as
    ``message.tool_calls[{id, function:{name, arguments}}]``; we execute
    each, append the assistant message verbatim plus one
    ``{"role": "tool", "tool_call_id": …}`` result message, and re-call
    up to N rounds.  Mirrors the role/permission enforcement of the
    Gemini and Anthropic paths.  ``reasoning_content`` (or inline
    ``<think>``) is captured per round and streamed as ``thinking``
    events.  Any endpoint that rejects the ``tools`` field falls back
    to the chat-only path — same answer quality as before this loop
    existed, never worse.
    """
    import asyncio
    import os
    import requests
    from google.auth.transport.requests import Request

    from capabilities.ai.registry import (
        _maas_base_url,
        _get_credentials,
        model_temperature,
    )
    from capabilities.ai.generation import _parse_openai_compat_reply

    async def _chat_only() -> dict:
        from capabilities.ai.generation import get_last_reasoning
        text, usage = await ask_ai(
            question, vehicle_context, user_id=user_id,
            account_id=account_id, language=language,
            user_context=user_context,
        )
        return {"text": text, "tool_results": [], "usage": usage,
                "reasoning": get_last_reasoning()}

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        return await _chat_only()
    creds.refresh(Request())

    location = model_info.get("locations", ["us-central1"])[0]
    maas_model_id = model_info["maas_model_id"]
    max_tokens = min(model_info.get("max_output_tokens", 4096), 8192)
    max_tool_rounds = _resolve_tool_rounds(model_info, _DEFAULT_TOOL_ROUNDS_OPENAI)
    _agent_temperature = model_temperature(model_info)
    extra_body = model_info.get("extra_body") or {}

    # Cache + history lookups mirror the Gemini/Anthropic paths.
    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(vehicle_context) if not has_history else ""
    ck = _cache_key(
        question, snap_h, model_name,
        account_id=account_id or 0,
        user_id=user_id or 0,
        language=language,
    ) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            if user_id is not None:
                _store_history(user_id, question, cached, account_id=account_id or 0)
            return {"text": cached, "tool_results": [], "usage": None}

    user_role = user_context.get("role") if user_context else None
    tools = await _get_openai_tools(
        role=user_role, account_id=account_id,
        scoped=_effective_scoped_flag(user_context, user_role),
    )

    system_prompt = ASSISTANT_SYSTEM
    if language and language != "en":
        from capabilities.localization.i18n import LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(language, language)
        system_prompt += (
            f"\n\nIMPORTANT: You MUST respond in {lang_name}. "
            f"All your output text must be in {lang_name}."
        )

    history = _chat_histories.get((user_id or 0, account_id or 0)) if user_id else None
    user_prompt = _build_agent_user_prompt(question, vehicle_context, user_context, history)

    url = _maas_base_url(location, project)
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_results: list[dict] = []
    reasoning_chunks: list[str] = []
    usage_total = {"prompt_tokens": 0, "reply_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}

    from capabilities.ai.usage import (
        record_call_attempt as _record_call,
        classify_error as _classify_err,
        classify_prompt as _classify_prompt,
    )
    import time as _t
    _prompt_category = _classify_prompt(question)

    def _post(body: dict) -> dict:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    # Streaming rounds — the model's ACTUAL reasoning flows to the chat
    # as it's generated (30-60s R1 rounds otherwise show a canned phrase
    # the whole time).  Thinking deltas are bridged from the requests
    # thread back onto the event loop, same pattern as the Gemini
    # streaming bridge.  Falls back to plain POST per-round if the
    # endpoint rejects the stream options.
    _loop = asyncio.get_running_loop()
    _streamed_any = {"flag": False}

    def _emit_thinking_blocking(text: str) -> None:
        if event_callback is None:
            return
        _streamed_any["flag"] = True
        try:
            asyncio.run_coroutine_threadsafe(
                event_callback({"type": "thinking", "text": text}), _loop,
            ).result()
        except Exception:
            pass

    def _post_stream(body: dict) -> dict:
        b = {**body, "stream": True, "stream_options": {"include_usage": True}}
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
            },
            json=b,
            timeout=180,
            stream=True,
        )
        r.raise_for_status()
        message, usage = _consume_openai_stream(
            r.iter_lines(decode_unicode=True),
            on_thinking=_emit_thinking_blocking if event_callback else None,
        )
        return {"choices": [{"message": message}], "usage": usage}

    use_stream = event_callback is not None
    final_text = ""
    final_reasoning_tail = ""
    for _round in range(max_tool_rounds):
        body = {
            "model": maas_model_id,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": _agent_temperature,
            "top_p": 0.8,
            **extra_body,
        }
        _started = _t.monotonic()
        _streamed_any["flag"] = False
        try:
            if use_stream:
                try:
                    data = await asyncio.to_thread(_post_stream, body)
                except Exception as stream_exc:
                    # Endpoint may not accept stream/stream_options —
                    # degrade to plain POST for the rest of the turn.
                    logger.info(
                        "OpenAI-compat stream failed (%s); using non-stream",
                        stream_exc,
                    )
                    use_stream = False
                    _streamed_any["flag"] = False
                    data = await asyncio.to_thread(_post, body)
            else:
                data = await asyncio.to_thread(_post, body)
        except Exception as e:
            latency_ms = int((_t.monotonic() - _started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=user_role, action="question",
                model=model_name, latency_ms=latency_ms,
                error_type=_classify_err(e),
                usage=None,
                prompt_category=_prompt_category,
            )
            logger.warning(
                "OpenAI-compat agent call failed (round %d), falling back: %s",
                _round, e,
            )
            # First-round failure may mean "endpoint rejects tools" —
            # the chat-only fallback preserves pre-loop behaviour.
            result = await _chat_only()
            result["tool_results"] = tool_results
            return result

        latency_ms = int((_t.monotonic() - _started) * 1000)
        text, usage, reasoning = _parse_openai_compat_reply(data)
        if usage:
            for k in usage_total:
                usage_total[k] += usage.get(k, 0) or 0
        await _record_call(
            account_id=account_id, user_id=user_id,
            role=user_role, action="question",
            model=model_name, latency_ms=latency_ms,
            error_type="ok",
            usage=usage,
            prompt_category=_prompt_category,
        )

        if reasoning:
            reasoning_chunks.append(reasoning)
            # Emit whole only when it WASN'T already streamed live —
            # otherwise the client's timeline would show it twice.
            if event_callback is not None and not _streamed_any["flag"]:
                try:
                    await event_callback({"type": "thinking", "text": reasoning})
                except Exception:
                    pass

        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            final_text = text
            break

        # Append the assistant message (the API requires the tool_calls
        # echoed back) WITHOUT ``reasoning_content`` — DeepSeek-style
        # endpoints emit that field but 400 when it's sent back as
        # input.  Then one tool message per call.
        messages.append({k: v for k, v in msg.items() if k != "reasoning_content"})
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            tool_name = fn.get("name", "")
            tc_id = tc.get("id", "")
            try:
                tool_args = json.loads(fn.get("arguments") or "{}")
            except (TypeError, ValueError):
                tool_args = {}
            logger.info("AI agent (openai-compat) calling tool: %s(%s)", tool_name, tool_args)

            blocked = await _check_tool_permission(
                tool_name, tool_args, user_role, user_context, account_id,
            )
            if blocked is not None:
                tool_results.append({"tool": tool_name, "args": tool_args, "data": blocked})
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(blocked, default=str),
                })
                continue

            if event_callback is not None:
                try:
                    await event_callback({
                        "type": "tool",
                        "name": tool_name,
                        "label": _TOOL_LABELS.get(tool_name, tool_name),
                    })
                except Exception:
                    pass
            try:
                result = await _execute_tool(
                    tool_name, tool_args, samsara_client,
                    account_id=account_id, db=db,
                    scope_vehicles=_scoped_vehicle_set(user_context, user_role),
                )
            except Exception as e:
                result = {"error": f"Tool execution failed: {e}"}
            tool_results.append({"tool": tool_name, "args": tool_args, "data": result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": json.dumps(result, default=str)[:20000],
            })
        # Loop continues — the model sees the tool results next round.
        final_reasoning_tail = text  # non-empty text alongside tool calls is rare; keep last
    else:
        # Ran out of rounds while the model kept calling tools — use
        # whatever text the last round produced rather than nothing.
        final_text = final_reasoning_tail

    if not final_text:
        final_text = (
            "I couldn't generate a response for that question. "
            "Please try rephrasing it."
        )

    usage_out = usage_total if any(usage_total.values()) else None
    if user_id is not None:
        _store_history(user_id, question, final_text, account_id=account_id or 0)
    if ck and not has_history:
        _cache_put(ck, final_text)
    return {
        "text": final_text, "tool_results": tool_results, "usage": usage_out,
        "reasoning": "\n\n".join(reasoning_chunks).strip(),
    }


async def _gemini_streamed_call(model, contents, tools, emit):
    """Run a Gemini call via ``generate_content_stream``, emitting live
    ``thinking`` (reasoning) and ``delta`` (answer) events through ``emit``,
    and return a response-shaped object the agent loop consumes unchanged.

    The synthesized response exposes ``.candidates[0].content`` (a real
    ``Content`` so a tool turn can be re-fed verbatim), ``.usage_metadata``
    (for ``_capture_usage``), and ``.text``.  Answer chunks are merged into a
    single text part so the loop's first-text-part extraction returns the
    *whole* reply, not just the first chunk.  Thought parts are streamed out
    but deliberately kept out of the synthesized parts — they're reasoning,
    not answer or re-feed content.
    """
    import asyncio as _a

    from google.genai import types as _gt

    loop = _a.get_running_loop()
    answer: list[str] = []
    fc_parts: list = []
    usage_holder: dict = {"meta": None}

    def _emit_blocking(ev: dict):
        # Bridge the sync streaming thread back onto the event loop so the
        # callback (which enqueues for the SSE generator) runs in order.
        try:
            _a.run_coroutine_threadsafe(emit(ev), loop).result()
        except Exception:
            pass

    def _run():
        for chunk in model.generate_content_stream(contents, tools=tools):
            um = getattr(chunk, "usage_metadata", None)
            if um is not None:
                usage_holder["meta"] = um
            for cand in (getattr(chunk, "candidates", None) or []):
                content = getattr(cand, "content", None)
                for p in (getattr(content, "parts", None) or []):
                    if getattr(p, "function_call", None):
                        fc_parts.append(p)
                        continue
                    try:
                        txt = p.text
                    except (ValueError, AttributeError):
                        txt = None
                    if not txt:
                        continue
                    if getattr(p, "thought", False):
                        _emit_blocking({"type": "thinking", "text": txt})
                    else:
                        answer.append(txt)
                        _emit_blocking({"type": "delta", "text": txt})

    await _a.to_thread(_run)

    full_text = "".join(answer)
    parts = list(fc_parts)
    if full_text:
        parts.append(_gt.Part.from_text(text=full_text))
    content = _gt.Content(role="model", parts=parts)
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=content)],
        usage_metadata=usage_holder["meta"],
        text=full_text,
    )


async def ask_agent(question: str, vehicle_context: dict,
                    samsara_client,
                    user_id: int | None = None,
                    account_id: int | None = None,
                    db=None,
                    language: str = "en",
                    user_context: dict | None = None,
                    event_callback=None) -> dict:
    """Agent-mode: AI can call Samsara tools to answer questions."""
    import asyncio

    try:
        from google.genai import types as _gtypes
    except ImportError:
        # ``ask_ai`` returns ``(text, usage)`` since 60e6577; unpack
        # both so the response dict carries the same shape every
        # other ask_agent return path produces.  Previously dropped
        # the tuple straight into the response, which JSON-serialised
        # the usage dict alongside the text and broke clients.
        text, usage = await ask_ai(question, vehicle_context, user_id=user_id,
                               account_id=account_id, language=language,
                               user_context=user_context)
        return {"text": text, "tool_results": [], "usage": usage}
    Part = _gtypes.Part
    Content = _gtypes.Content

    cur_model_name = get_current_model_name()
    # User-level model preference → account-level → global
    if user_id is not None and user_id in _user_models:
        cur_model_name = _user_models[user_id][0]
    elif account_id is not None and account_id in _account_models:
        cur_model_name = _account_models[account_id][0]

    from capabilities.ai.registry import MODEL_REGISTRY
    _info = MODEL_REGISTRY.get(cur_model_name, {})
    _api_type = _info.get("api_type", "gemini")

    # Anthropic on Vertex supports native function-calling — route to its
    # own loop instead of falling through to text-only ``ask_ai``.
    if _api_type == "anthropic":
        return await _run_anthropic_agent(
            question, vehicle_context, samsara_client,
            model_name=cur_model_name, model_info=_info,
            user_id=user_id, account_id=account_id, db=db,
            language=language, user_context=user_context,
            event_callback=event_callback,
        )

    # OpenAI-compat MaaS models (DeepSeek/Qwen/Kimi/Grok/gpt-oss) get
    # their own function-calling loop — the Reasoning tier can call
    # tools like the Gemini/Anthropic tiers instead of answering from
    # the snapshot alone.  A per-model registry opt-out
    # (``supports_tools: False``) or any tools-rejection error degrades
    # to the chat-only path inside the loop.
    if _api_type == "openai_compat" and _info.get("supports_tools", True):
        return await _run_openai_compat_agent(
            question, vehicle_context, samsara_client,
            model_name=cur_model_name, model_info=_info,
            user_id=user_id, account_id=account_id, db=db,
            language=language, user_context=user_context,
            event_callback=event_callback,
        )

    # mistral_raw (and opted-out models) → no FC support; chat-only.
    if _api_type != "gemini":
        text, usage = await ask_ai(question, vehicle_context, user_id=user_id,
                               account_id=account_id, language=language,
                               user_context=user_context)
        # Reasoning models produced a chain-of-thought the raw generator
        # captured on the task-local side channel — attach it so the
        # chat can show it instead of silently discarding it.
        from capabilities.ai.generation import get_last_reasoning
        return {"text": text, "tool_results": [], "usage": usage,
                "reasoning": get_last_reasoning()}

    model, cur_model_name, _ = get_model_for_user(user_id, account_id)

    from capabilities.ai.registry import MODEL_REGISTRY as _MODEL_REGISTRY
    max_tool_rounds = _resolve_tool_rounds(
        _MODEL_REGISTRY.get(cur_model_name, {}), _DEFAULT_TOOL_ROUNDS_GEMINI
    )

    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(vehicle_context) if not has_history else ""
    ck = _cache_key(
        question, snap_h, cur_model_name,
        account_id=account_id or 0,
        user_id=user_id or 0,
        language=language,
    ) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for ask_agent()")
            if user_id is not None:
                _store_history(user_id, question, cached, account_id=account_id or 0)
            return {"text": cached, "tool_results": []}

    user_role = user_context.get("role") if user_context else None
    tools = await _get_cached_tools(
        role=user_role, account_id=account_id,
        scoped=_effective_scoped_flag(user_context, user_role),
    )

    # Per-attempt telemetry — write one ai_usage row per model call
    # (initial + each post-tool re-call) so the router sees this
    # high-volume path at the same resolution as the generate() paths.
    # Previously only the route-level external log fired (one row per
    # turn) which masked which model attempts hit 429 / content_filter
    # / timed out inside the agent loop.
    from capabilities.ai.usage import (
        record_call_attempt as _record_call,
        classify_error as _classify_err,
        classify_prompt as _classify_prompt,
    )
    import time as _t
    _prompt_category = _classify_prompt(question)

    # Live streaming gate: only for the Gemini path, only when a stream
    # consumer is attached (event_callback), and only when the env flag is on.
    # Off → the model call is the unchanged non-streaming generate_content.
    _stream_on = _STREAM_TOKENS and event_callback is not None and _api_type == "gemini"

    async def _call_model_with_telemetry(*args, **kwargs):
        """Wrap the model call with timing + ai_usage row.

        When streaming is on, the call routes through ``_gemini_streamed_call``
        (emits live thinking/delta events, returns a response-shaped object);
        otherwise it's the unchanged ``generate_content`` in a thread.  Both
        record one ai_usage attempt row.
        """
        started = _t.monotonic()
        try:
            if _stream_on:
                resp = await _gemini_streamed_call(
                    model, args[0], kwargs.get("tools"), event_callback,
                )
            else:
                resp = await asyncio.to_thread(
                    model.generate_content, *args, **kwargs,
                )
            latency_ms = int((_t.monotonic() - started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=user_role, action="question",
                model=cur_model_name, latency_ms=latency_ms,
                error_type="ok",
                usage=_capture_usage(resp),
                prompt_category=_prompt_category,
            )
            return resp
        except Exception as e:
            latency_ms = int((_t.monotonic() - started) * 1000)
            await _record_call(
                account_id=account_id, user_id=user_id,
                role=user_role, action="question",
                model=cur_model_name, latency_ms=latency_ms,
                error_type=_classify_err(e),
                usage=None,
                prompt_category=_prompt_category,
            )
            raise

    system_prompt = ASSISTANT_SYSTEM
    if language and language != "en":
        from capabilities.localization.i18n import LANGUAGE_NAMES
        lang_name = LANGUAGE_NAMES.get(language, language)
        system_prompt += f"\n\nIMPORTANT: You MUST respond in {lang_name}. All your output text must be in {lang_name}."

    parts = [system_prompt, "\n\n"]

    # Inject user identity
    if user_context:
        uc = user_context
        profile_lines = ["User profile:"]
        if uc.get("name"):
            profile_lines.append(f"- Name: {uc['name']}")
        if uc.get("role"):
            profile_lines.append(f"- Role: {uc['role']}")
        if uc.get("vehicle_nums") and len(uc["vehicle_nums"]) > 0:
            profile_lines.append(f"- Assigned trucks: {', '.join(uc['vehicle_nums'])}")
        elif uc.get("vehicle_num"):
            profile_lines.append(f"- Assigned vehicle: {uc['vehicle_num']}")
        if uc.get("timezone"):
            profile_lines.append(f"- Timezone: {uc['timezone']}")
        # Anchor "now" — kills the date-guessing spirals on date-relative
        # questions (see current_datetime_line's docstring).
        from capabilities.ai.usage import current_datetime_line
        profile_lines.append(current_datetime_line(uc.get("timezone")))
        # Dynamic permission guidance from ROLE_PERMISSIONS (with per-account override)
        if uc.get("role"):
            from capabilities.permissions.roles import build_role_guidance_for_account
            _db = user_context.get("_db")
            _account_id = account_id or 0
            guidance = await build_role_guidance_for_account(_db, _account_id, uc["role"])
            profile_lines.append(f"\n{guidance}")

        # Driver isolation: strict truck access
        if uc.get("role") == "driver" and (uc.get("vehicle_nums") or uc.get("vehicle_num")):
            trucks = uc.get("vehicle_nums") or [uc["vehicle_num"]]
            trucks_str = ", ".join(trucks)
            profile_lines.append(
                f"\nCRITICAL DRIVER RESTRICTION:"
                f"\nYou are a driver assigned to truck(s): {trucks_str} ONLY."
                f"\n- You MUST ONLY query data for your assigned trucks."
                f"\n- NEVER look up, mention, or reveal data about any other truck."
                f"\n- When calling any tool, always use vehicle_name for one of your assigned trucks."
                f"\n- Do NOT call account-wide tools (whole-account summaries, all trucks, etc.)."
                f"\n- If the user asks about another truck or account-wide totals, politely decline."
            )
        parts.append("\n".join(profile_lines) + "\n\n")
        parts.append(_render_page_context(user_context))

    if vehicle_context:
        data_str = json.dumps(vehicle_context, separators=(',', ':'), default=str)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Vehicle snapshot:\n```\n{data_str}\n```\n\n")

    parts.append(
        "You have access to tools that can fetch live data from Samsara. "
        "Use them when the user asks about specific trucks, faults, fuel, "
        "driver efficiency, scorecards, safety events, maintenance, "
        "fuel costs, rolling/stopped status, or cameras. "
        "For safety events, always state the time period checked. "
        "For camera checks, only check one truck at a time — for account-wide "
        "camera checks, direct the user to the Camera Check menu. "
        "If the snapshot already has enough info, answer directly without "
        "calling tools.\n\n"
        "IMPORTANT: If a tool returns an 'error' field in its result, you "
        "MUST report that error to the user honestly. Never hide tool "
        "failures or pretend the data is fine. Tell the user something "
        "like 'I tried to check X but got an error: [message]'.\n\n"
    )

    if user_id and (user_id, account_id or 0) in _chat_histories:
        history = _chat_histories[(user_id, account_id or 0)]
        parts.append("Previous conversation:\n")
        for entry in history:
            parts.append(f"{entry['role']}: {entry['text']}\n")
        parts.append("\n")

    parts.append(f"User question: {question}")
    full_prompt = "".join(parts)
    tool_results: list[dict] = []

    max_retries = 2
    last_exc = None

    for attempt in range(max_retries + 1):
        try:
            response = await _call_model_with_telemetry(
                full_prompt, tools=tools,
            )

            for _round in range(max_tool_rounds):
                if not response.candidates:
                    return {
                        "text": (
                            "I couldn't generate a response for that question. "
                            "Please try rephrasing it."
                        ),
                        "tool_results": tool_results,
                        "usage": _capture_usage(response),
                    }

                candidate = response.candidates[0]
                # Gemini 2.5 Flash with thinking enabled prepends thought parts
                # (Parts with thought_signature but no text/function_call) before
                # the actual content.  Accessing .text on such a part raises
                # ValueError in the SDK, so we scan all parts safely.
                _text_part = None
                _fc_part = None
                for _p in candidate.content.parts:
                    if _fc_part is None and _p.function_call:
                        _fc_part = _p
                    if _text_part is None:
                        try:
                            _pt = _p.text
                        except (ValueError, AttributeError):
                            _pt = None
                        if _pt:
                            _text_part = _p

                if _text_part is not None:
                    text = _text_part.text.strip()
                    usage = _capture_usage(response)
                    if user_id is not None:
                        _store_history(user_id, question, text, account_id=account_id or 0)
                    if ck and not has_history:
                        _cache_put(ck, text)
                    return {"text": text, "tool_results": tool_results, "usage": usage}

                part = _fc_part if _fc_part is not None else candidate.content.parts[0]

                if part.function_call:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info(f"AI agent calling tool: {tool_name}({tool_args})")

                    # Safety-net: enforce role + isolation on tool execution
                    # through the shared account-aware gate — one source of
                    # truth with the Anthropic path and the API permission
                    # model (per-account overrides + module masking included).
                    result = await _check_tool_permission(
                        tool_name, tool_args, user_role, user_context, account_id,
                    )
                    _blocked = result is not None

                    if _blocked:
                        tool_results.append({"tool": tool_name, "args": tool_args, "data": result})
                        fn_response = Part.from_function_response(
                            name=tool_name,
                            response={"result": result},
                        )
                        response = await _call_model_with_telemetry(
                            [
                                Content(parts=[Part.from_text(text=full_prompt)], role="user"),
                                candidate.content,
                                Content(parts=[fn_response], role="user"),
                            ],
                            tools=tools,
                        )
                        candidate = response.candidates[0]
                        continue

                    if event_callback is not None:
                        try:
                            await event_callback({"type": "tool", "name": tool_name, "label": _TOOL_LABELS.get(tool_name, tool_name)})
                        except Exception:
                            pass
                    result = await _execute_tool(
                        tool_name, tool_args, samsara_client,
                        account_id=account_id, db=db,
                        scope_vehicles=_scoped_vehicle_set(user_context, user_role),
                    )
                    tool_results.append({"tool": tool_name, "args": tool_args, "data": result})

                    fn_response = Part.from_function_response(
                        name=tool_name,
                        response={"result": result},
                    )
                    response = await _call_model_with_telemetry(
                        [
                            Content(parts=[Part.from_text(text=full_prompt)], role="user"),
                            candidate.content,
                            Content(parts=[fn_response], role="user"),
                        ],
                        tools=tools,
                    )
                else:
                    break

            if response.candidates and response.candidates[0].content.parts:
                try:
                    text = response.text.strip()
                except ValueError:
                    parts_list = response.candidates[0].content.parts
                    _texts: list[str] = []
                    for _p in parts_list:
                        try:
                            _texts.append(_p.text or '')
                        except (ValueError, AttributeError):
                            pass
                    text = ''.join(_texts).strip()
                usage = _capture_usage(response)
                if user_id is not None:
                    _store_history(user_id, question, text, account_id=account_id or 0)
                if ck and not has_history:
                    _cache_put(ck, text)
                return {"text": text, "tool_results": tool_results, "usage": usage}

            text, usage = await ask_ai(question, vehicle_context, user_id=user_id,
                                   account_id=account_id, language=language,
                                   user_context=user_context)
            return {"text": text, "tool_results": tool_results, "usage": usage}

        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if '429' in err_str or 'resource exhausted' in err_str:
                # ONE quick same-model retry covers per-second blips.  A
                # second 429 means the quota WINDOW is exhausted — more
                # same-model retries with exponential sleeps just stack
                # dead seconds onto the turn (and, pre-first-byte, walk
                # the SSE into nginx's 504).  Bench the model so the
                # next turn stages the tier's fallback with tools, and
                # fall back now for this one.
                if attempt < 1:
                    logger.warning("Agent rate limited — one quick retry in 1s")
                    await asyncio.sleep(1)
                    continue
                from capabilities.ai.models import report_quota_exhausted
                report_quota_exhausted(cur_model_name)
            logger.warning(f"Agent mode failed, falling back: {e}")
            text, usage = await ask_ai(question, vehicle_context, user_id=user_id,
                                   account_id=account_id, language=language,
                                   user_context=user_context)
            return {"text": text, "tool_results": tool_results, "usage": usage}

    logger.warning(f"Agent exhausted retries, falling back: {last_exc}")
    text, usage = await ask_ai(question, vehicle_context, user_id=user_id,
                           account_id=account_id, language=language,
                           user_context=user_context)
    return {"text": text, "tool_results": tool_results, "usage": usage}


async def ask_agent_stream(question: str, vehicle_context: dict,
                           samsara_client,
                           user_id: int | None = None,
                           account_id: int | None = None,
                           db=None,
                           language: str = "en",
                           user_context: dict | None = None):
    """Async generator version of ask_agent that streams tool events then the final reply.

    Yields dicts:
      {"type": "tool",  "name": str, "label": str}   — each tool call
      {"type": "done",  "reply": str, "suggestions": list, "usage": dict | None,
       "tool_results": list}                          — final result
      {"type": "error", "message": str}               — on unrecoverable failure
    """
    import asyncio as _asyncio

    from capabilities.ai.usage import parse_ai_suggestions as _parse_sug

    queue: _asyncio.Queue = _asyncio.Queue()

    # Ordered process timeline — the step log the chat renders under
    # "Thought process" (thinking → tool → thinking → …, in the order
    # it actually happened).  Every agent loop (Gemini / Anthropic /
    # OpenAI-compat) funnels its events through this one callback, so
    # accumulating here covers all of them with a single site.
    # Consecutive thinking chunks coalesce into one step (Gemini
    # streams token-sized fragments).
    process: list[dict] = []

    # Per-tool-step wall-clock: a tool step's duration ≈ the gap from its
    # own event to the NEXT event (tool exec + the model round-trip that
    # consumes the result).  We stamp elapsed_ms on the pending tool step
    # when any new event arrives, and close the last one at finish.
    import time as _dur_time
    _pending_tool: dict = {"step": None, "ts": 0.0}

    def _close_pending(now: float) -> None:
        step = _pending_tool["step"]
        if step is not None:
            step["elapsed_ms"] = max(0, int((now - _pending_tool["ts"]) * 1000))
            _pending_tool["step"] = None

    async def _callback(event: dict):
        etype = event.get("type")
        now = _dur_time.monotonic()
        if etype in ("thinking", "tool"):
            _close_pending(now)
        if etype == "thinking":
            if process and process[-1].get("type") == "thinking":
                process[-1]["text"] += event.get("text", "")
            else:
                process.append({"type": "thinking",
                                "text": event.get("text", "")})
        elif etype == "tool":
            step = {"type": "tool",
                    "name": event.get("name", ""),
                    "label": event.get("label", "")}
            process.append(step)
            _pending_tool["step"] = step
            _pending_tool["ts"] = now
        await queue.put(event)

    def _finish_process(result: dict) -> list[dict]:
        """Zip tool results into the tool steps + digest them.

        ``result["tool_results"]`` appends in execution order — the
        same order the tool events fired — so index-zipping is exact.
        Results are digested (truncated JSON) because full tool payloads
        can be tens of KB; the timeline needs a glance, not the data.
        """
        _close_pending(_dur_time.monotonic())  # stamp the final tool step
        tool_steps = [s for s in process if s["type"] == "tool"]
        for step, tr in zip(tool_steps, result.get("tool_results") or []):
            try:
                digest = json.dumps(tr.get("data"), default=str)
            except (TypeError, ValueError):
                digest = str(tr.get("data"))
            if len(digest) > 400:
                digest = digest[:400] + "…"
            step["result"] = digest
            if tr.get("args"):
                try:
                    step["args"] = json.dumps(tr["args"], default=str)[:200]
                except (TypeError, ValueError):
                    pass
        # Chat-only paths emit no thinking events but may return the
        # whole chain-of-thought on the result — surface it as one step.
        if not any(s["type"] == "thinking" for s in process) and result.get("reasoning"):
            process.insert(0, {"type": "thinking", "text": result["reasoning"]})
        return process

    def _collect_artifacts(result: dict) -> list[dict]:
        """Gather artifacts a tool attached to its result envelope.

        A tool opts in by returning ``{"artifacts": [ {type, …}, … ]}`` in
        its normal result dict (see ``tool_ok(..., artifacts=[...])``).
        We flatten them in tool-execution order across all tool calls.
        Malformed entries are skipped — a bad artifact must never break
        the answer.  Capped so a runaway tool can't flood the client.
        """
        out: list[dict] = []
        for tr in result.get("tool_results") or []:
            data = tr.get("data")
            if not isinstance(data, dict):
                continue
            arts = data.get("artifacts")
            if not isinstance(arts, list):
                continue
            for a in arts:
                if isinstance(a, dict) and a.get("type"):
                    out.append(a)
                if len(out) >= 8:
                    return out
        return out

    async def _run():
        try:
            result = await ask_agent(
                question, vehicle_context, samsara_client,
                user_id=user_id, account_id=account_id,
                db=db, language=language, user_context=user_context,
                event_callback=_callback,
            )
            reply = result.get("text", "")
            clean, suggestions = _parse_sug(reply)
            # Attribute THIS answer to the tier that produced it ("Fast" /
            # "Thinking" / "Reasoning"), frozen at receipt so switching the
            # tier picker never relabels history.  The raw model id
            # ("deepseek-r1") deliberately does NOT leave the server on this
            # path — users picked a tier, so the bubble speaks tier; the
            # real model stays in the ai_usage rows the operator console
            # and router analytics read.
            try:
                _answer_model = result.get("model") or get_model_for_user(user_id, account_id)[1]
            except Exception:
                _answer_model = result.get("model") or ""
            from capabilities.ai.registry import get_model_tier_label
            await queue.put({
                "type": "done",
                "reply": clean,
                "suggestions": suggestions,
                "usage": result.get("usage"),
                "tool_results": result.get("tool_results", []),
                "model_tier": get_model_tier_label(_answer_model) or "",
                # Full chain-of-thought for models that return it whole
                # (reasoning tier / Claude thinking).  Gemini reasoning
                # streams live as `thinking` events instead; the client
                # keeps whichever it received.
                "reasoning": result.get("reasoning") or "",
                # Ordered step timeline (thinking + tool calls with
                # result digests) — the "N steps" process log.
                "process": _finish_process(result),
                # Structured artifacts (tables/charts) a tool attached to
                # its result — rendered natively by the client instead of
                # flattened to prose.  Display-only + browser-local, like
                # the process timeline.
                "artifacts": _collect_artifacts(result),
            })
        except Exception as exc:
            # The route layer (``_event_stream`` in interfaces/api/routes/ai.py)
            # scrubs this message before it leaves the server, so passing the
            # raw text here is safe for now — but keep it free of credentials
            # we already know about.
            logger.exception("ask_agent failed in stream wrapper")
            await queue.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    task = _asyncio.create_task(_run())
    try:
        while True:
            event = await queue.get()
            yield event
            if event["type"] in ("done", "error"):
                break
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except _asyncio.CancelledError:
                pass
