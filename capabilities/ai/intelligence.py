"""AI intelligence: diagnose, summarise, ask, and agent mode."""

from __future__ import annotations

import json
import logging

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
    AI_TOOLS,  # backward compat re-export
)
from capabilities.iam.permissions import TOOL_PERMISSIONS, ACCOUNT_WIDE_TOOLS, VEHICLE_SPECIFIC_TOOLS

logger = logging.getLogger("bot.ai")

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
    "get_driver_scorecard":   "Getting driver scorecard",
    "get_drivers_list":       "Listing drivers",
    "check_vehicle_camera":   "Checking camera",
    "get_geofences":          "Checking geofences",
    "get_weather":            "Getting weather",
    "get_vehicle_odometer":   "Reading odometer",
    "search_vehicles":        "Searching vehicles",
    "search_knowledge_base":  "Searching knowledge base",
    "get_account_stats":      "Getting fleet stats",
    "get_idle_vehicles":      "Checking long-idle vehicles",
    "get_driver_hos_status":  "Reading driver hours",
    "get_alert_history":      "Reviewing alert history",
    "get_recent_work_orders": "Looking up shop visits",
    "get_recent_inspections": "Reviewing inspections",
    "get_vehicle_history":    "Reading vehicle history",
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
    from features.vehicles.service import get_fleet_overview as _svc_fleet_overview
    from capabilities.telemetry.service import get_vehicle_health as _svc_vehicle_health
    from features.events.service import get_events as _svc_get_events
    snapshot: dict = {}

    try:
        fleet = await _svc_fleet_overview(account_id)
        if _vehicle_set:
            fleet = [
                v for v in fleet
                if v.get("name", "").lower() in _vehicle_set
            ]

        snapshot["total_vehicles"] = len(fleet)
        snapshot["vehicles"] = []
        for v in fleet[:50]:
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
        logger.error(f"AI fleet snapshot failed: {e}")
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
            active = [t for t in tasks if t.get("status") in ("pending", "overdue")]
            if active:
                snapshot["maintenance"] = {
                    "pending": sum(1 for t in active if t["status"] == "pending"),
                    "overdue": sum(1 for t in active if t["status"] == "overdue"),
                    "tasks": [
                        {
                            "vehicle": t.get("vehicle_name", "?"),
                            "type": t.get("task_type", "custom"),
                            "status": t.get("status"),
                            "due_date": t.get("due_date"),
                            "due_miles": t.get("due_miles"),
                        }
                        for t in active[:10]
                    ],
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


async def generate_summary(fleet_data: dict,
                           account_id: int | None = None,
                           language: str = "en",
                           user_context: dict | None = None,
                           ) -> tuple[str, dict | None]:
    """Generate an AI executive summary of fleet status.

    Returns ``(summary_text, usage)``.
    """
    prompt = "Generate a morning fleet status briefing from this data."
    return await generate(prompt, system=SUMMARY_SYSTEM,
                          context_data=fleet_data, account_id=account_id,
                          language=language, user_context=user_context,
                          action="summary")


async def ask_ai(question: str, fleet_context: dict,
                 user_id: int | None = None,
                 account_id: int | None = None,
                 language: str = "en",
                 user_context: dict | None = None,
                 ) -> tuple[str, dict | None]:
    """Answer a natural-language question using AI.

    Returns ``(answer_text, usage)``.
    """
    return await generate(question, system=ASSISTANT_SYSTEM,
                          context_data=fleet_context, user_id=user_id,
                          account_id=account_id, language=language,
                          user_context=user_context,
                          action="question")


# ── Function-Calling Agent ───────────────────────────────────────


def _build_agent_user_prompt(
    question: str,
    fleet_context: dict | None,
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
        if uc.get("role") == "driver" and (uc.get("vehicle_nums") or uc.get("vehicle_num")):
            trucks = uc.get("vehicle_nums") or [uc["vehicle_num"]]
            trucks_str = ", ".join(trucks)
            profile_lines.append(
                f"\nCRITICAL DRIVER RESTRICTION:"
                f"\nYou are a driver assigned to truck(s): {trucks_str} ONLY."
                f"\n- You MUST ONLY query data for your assigned trucks."
                f"\n- NEVER look up, mention, or reveal data about any other truck."
                f"\n- When calling any tool, always use vehicle_name for one of your assigned trucks."
                f"\n- Do NOT call fleet-wide tools (fleet summary, all trucks, etc.)."
                f"\n- If the user asks about another truck or fleet totals, politely decline."
            )
        parts.append("\n".join(profile_lines) + "\n\n")

    if fleet_context:
        data_str = json.dumps(fleet_context, separators=(',', ':'), default=str)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Fleet snapshot:\n```\n{data_str}\n```\n\n")

    parts.append(
        "You have access to tools that can fetch live data from Samsara. "
        "Use them when the user asks about specific trucks, faults, fuel, "
        "driver efficiency, driver scorecards, safety events, maintenance, "
        "fuel costs, rolling/stopped status, or cameras. "
        "For safety events, always state the time period checked. "
        "For camera checks, only check one truck at a time — for fleet-wide "
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


def _effective_scoped_flag(user_context: dict | None, user_role: str | None) -> bool:
    """True if the caller is vehicle/company-restricted — used to drop
    fleet-wide tools from what the model is advertised.  Mirrors the scope
    determination in ``_check_tool_permission`` so advertisement and gate agree.
    """
    if not user_context:
        return False
    if "scoped_vehicle_nums" in user_context:
        return user_context["scoped_vehicle_nums"] is not None
    return user_role == "driver"


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
    req_perms = TOOL_PERMISSIONS.get(tool_name)
    if req_perms is not None:
        try:
            from adapters.storage import Role
            role = Role(user_role)
            if account_id is not None:
                from capabilities.iam.permissions import get_account_permissions
                perms = await get_account_permissions(role, int(account_id))
            else:
                from capabilities.iam.permissions import get_permissions
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
    scoped: list | None = None
    if user_context is not None:
        if "scoped_vehicle_nums" in user_context:
            scoped = user_context["scoped_vehicle_nums"]
        elif user_role == "driver":
            trucks = user_context.get("vehicle_nums") or []
            if not trucks and user_context.get("vehicle_num"):
                trucks = [user_context["vehicle_num"]]
            scoped = [t for t in trucks if t]

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
        if tool_name in ACCOUNT_WIDE_TOOLS:
            return {
                "error": (
                    f"Access denied: {tool_name} returns fleet-wide data"
                    f" outside your access scope."
                ),
            }
    return None


async def _run_anthropic_agent(
    question: str,
    fleet_context: dict,
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
    )

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    creds = _get_credentials()
    if not project or not creds:
        text, usage = await ask_ai(
            question, fleet_context, user_id=user_id,
            account_id=account_id, language=language,
            user_context=user_context,
        )
        return {"text": text, "tool_results": [], "usage": usage}
    creds.refresh(Request())

    location = model_info.get("locations", ["global"])[0]
    anthropic_model_id = model_info["anthropic_model_id"]
    max_tokens = min(model_info.get("max_output_tokens", 4096), 8192)

    # Cache + history lookups mirror the Gemini path.
    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(fleet_context) if not has_history else ""
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
    user_prompt = _build_agent_user_prompt(question, fleet_context, user_context, history)

    url = _anthropic_url(location, project, anthropic_model_id)
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    tool_results: list[dict] = []
    usage_total = {"prompt_tokens": 0, "reply_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}

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
    for _round in range(4):  # 1 initial + up to 3 tool-use rounds
        body = {
            "anthropic_version": "vertex-2023-10-16",
            "system": system_prompt,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "top_p": 0.8,
        }
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
                question, fleet_context, user_id=user_id,
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
        # Collect text + tool_use blocks
        text_chunks: list[str] = []
        tool_use_blocks: list[dict] = []
        for block in content_blocks:
            btype = block.get("type")
            if btype == "text":
                text_chunks.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_use_blocks.append(block)

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
    return {"text": final_text, "tool_results": tool_results, "usage": usage_out}


async def ask_agent(question: str, fleet_context: dict,
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
        text, usage = await ask_ai(question, fleet_context, user_id=user_id,
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
            question, fleet_context, samsara_client,
            model_name=cur_model_name, model_info=_info,
            user_id=user_id, account_id=account_id, db=db,
            language=language, user_context=user_context,
            event_callback=event_callback,
        )

    # openai_compat / mistral_raw → no FC support; degrade to chat-only.
    if _api_type != "gemini":
        text, usage = await ask_ai(question, fleet_context, user_id=user_id,
                               account_id=account_id, language=language,
                               user_context=user_context)
        return {"text": text, "tool_results": [], "usage": usage}

    model, cur_model_name, _ = get_model_for_user(user_id, account_id)

    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(fleet_context) if not has_history else ""
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

    async def _call_model_with_telemetry(*args, **kwargs):
        """Wrap model.generate_content with timing + ai_usage row."""
        started = _t.monotonic()
        try:
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
        # Dynamic permission guidance from ROLE_PERMISSIONS (with per-account override)
        if uc.get("role"):
            from capabilities.iam.permissions import build_role_guidance_for_account
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
                f"\n- Do NOT call fleet-wide tools (fleet summary, all trucks, etc.)."
                f"\n- If the user asks about another truck or fleet totals, politely decline."
            )
        parts.append("\n".join(profile_lines) + "\n\n")

    if fleet_context:
        data_str = json.dumps(fleet_context, separators=(',', ':'), default=str)
        if len(data_str) > 30000:
            data_str = data_str[:30000] + "\n... (truncated)"
        parts.append(f"Fleet snapshot:\n```\n{data_str}\n```\n\n")

    parts.append(
        "You have access to tools that can fetch live data from Samsara. "
        "Use them when the user asks about specific trucks, faults, fuel, "
        "driver efficiency, driver scorecards, safety events, maintenance, "
        "fuel costs, rolling/stopped status, or cameras. "
        "For safety events, always state the time period checked. "
        "For camera checks, only check one truck at a time — for fleet-wide "
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

            for _round in range(3):
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

            text, usage = await ask_ai(question, fleet_context, user_id=user_id,
                                   account_id=account_id, language=language,
                                   user_context=user_context)
            return {"text": text, "tool_results": tool_results, "usage": usage}

        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if ('429' in err_str or 'resource exhausted' in err_str) and attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    f"Agent rate limited (attempt {attempt+1}/{max_retries+1}), "
                    f"retrying in {wait}s"
                )
                await asyncio.sleep(wait)
                continue
            logger.warning(f"Agent mode failed, falling back: {e}")
            text, usage = await ask_ai(question, fleet_context, user_id=user_id,
                                   account_id=account_id, language=language,
                                   user_context=user_context)
            return {"text": text, "tool_results": tool_results, "usage": usage}

    logger.warning(f"Agent exhausted retries, falling back: {last_exc}")
    text, usage = await ask_ai(question, fleet_context, user_id=user_id,
                           account_id=account_id, language=language,
                           user_context=user_context)
    return {"text": text, "tool_results": tool_results, "usage": usage}


async def ask_agent_stream(question: str, fleet_context: dict,
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

    async def _callback(event: dict):
        await queue.put(event)

    async def _run():
        try:
            result = await ask_agent(
                question, fleet_context, samsara_client,
                user_id=user_id, account_id=account_id,
                db=db, language=language, user_context=user_context,
                event_callback=_callback,
            )
            reply = result.get("text", "")
            clean, suggestions = _parse_sug(reply)
            await queue.put({
                "type": "done",
                "reply": clean,
                "suggestions": suggestions,
                "usage": result.get("usage"),
                "tool_results": result.get("tool_results", []),
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
