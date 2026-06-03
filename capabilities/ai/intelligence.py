"""AI intelligence: diagnose, summarise, ask, and agent mode."""

from __future__ import annotations

import json
import logging

from capabilities.ai.registry import _is_openai_compat
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
    from capabilities.vehicles.service import get_fleet_overview as _svc_fleet_overview
    from capabilities.telemetry.service import get_vehicle_health as _svc_vehicle_health
    from capabilities.events.service import get_events as _svc_get_events
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
                          user_context: dict | None = None) -> str:
    """AI-powered fault code diagnosis for a specific vehicle."""
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
                          language=language, user_context=user_context)


async def generate_summary(fleet_data: dict,
                           account_id: int | None = None,
                           language: str = "en",
                           user_context: dict | None = None) -> str:
    """Generate an AI executive summary of fleet status."""
    prompt = "Generate a morning fleet status briefing from this data."
    return await generate(prompt, system=SUMMARY_SYSTEM,
                          context_data=fleet_data, account_id=account_id,
                          language=language, user_context=user_context)


async def ask_ai(question: str, fleet_context: dict,
                 user_id: int | None = None,
                 account_id: int | None = None,
                 language: str = "en",
                 user_context: dict | None = None) -> str:
    """Answer a natural-language question using AI."""
    return await generate(question, system=ASSISTANT_SYSTEM,
                          context_data=fleet_context, user_id=user_id,
                          account_id=account_id, language=language,
                          user_context=user_context)


# ── Function-Calling Agent ───────────────────────────────────────


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
        text = await ask_ai(question, fleet_context, user_id=user_id,
                               account_id=account_id, language=language,
                               user_context=user_context)
        return {"text": text, "tool_results": []}
    Part = _gtypes.Part
    Content = _gtypes.Content

    cur_model_name = get_current_model_name()
    # User-level model preference → account-level → global
    if user_id is not None and user_id in _user_models:
        cur_model_name = _user_models[user_id][0]
    elif account_id is not None and account_id in _account_models:
        cur_model_name = _account_models[account_id][0]
    if _is_openai_compat(cur_model_name):
        text = await ask_ai(question, fleet_context, user_id=user_id,
                               account_id=account_id, language=language,
                               user_context=user_context)
        return {"text": text, "tool_results": []}

    model, cur_model_name, _ = get_model_for_user(user_id, account_id)

    has_history = bool(user_id and (user_id, account_id or 0) in _chat_histories)
    snap_h = _snapshot_hash(fleet_context) if not has_history else ""
    ck = _cache_key(question, snap_h, cur_model_name, account_id=account_id) if not has_history else ""
    if not has_history and ck:
        cached = _cache_get(ck)
        if cached is not None:
            logger.debug("Cache hit for ask_agent()")
            if user_id is not None:
                _store_history(user_id, question, cached, account_id=account_id or 0)
            return {"text": cached, "tool_results": []}

    user_role = user_context.get("role") if user_context else None
    tools = _get_cached_tools(role=user_role)

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
        if uc.get("department") and uc["department"] != "general":
            profile_lines.append(f"- Department: {uc['department']}")
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
            response = await asyncio.to_thread(
                model.generate_content, full_prompt, tools=tools,
            )

            for _round in range(3):
                if not response.candidates:
                    return {
                        "text": (
                            "I couldn't generate a response for that question. "
                            "Please try rephrasing it."
                        ),
                        "tool_results": tool_results,
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
                    _capture_usage(response)
                    if user_id is not None:
                        _store_history(user_id, question, text, account_id=account_id or 0)
                    if ck and not has_history:
                        _cache_put(ck, text)
                    return {"text": text, "tool_results": tool_results}

                part = _fc_part if _fc_part is not None else candidate.content.parts[0]

                if part.function_call:
                    fc = part.function_call
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}

                    logger.info(f"AI agent calling tool: {tool_name}({tool_args})")

                    # Safety-net: enforce role permissions on tool execution
                    _blocked = False
                    if user_role:
                        req_perms = TOOL_PERMISSIONS.get(tool_name)
                        if req_perms is not None:
                            try:
                                from capabilities.iam.permissions import get_permissions
                                from adapters.storage import Role
                                _perms = get_permissions(Role(user_role))
                                if not any(getattr(_perms, p, False) for p in req_perms):
                                    _blocked = True
                                    result = {
                                        "error": (
                                            f"Access denied: your role ({user_role})"
                                            f" cannot use {tool_name}."
                                        ),
                                    }
                            except (ValueError, KeyError, ImportError) as e:
                                logger.debug("Tool dispatch error for %s: %s", tool_name, e)
                        if not _blocked and user_role == "driver" and user_context:
                            assigned_trucks = user_context.get("vehicle_nums") or []
                            if not assigned_trucks and user_context.get("vehicle_num"):
                                assigned_trucks = [user_context["vehicle_num"]]
                            assigned_set = {t.strip().lower() for t in assigned_trucks if t}
                            if assigned_set:
                                if tool_name in VEHICLE_SPECIFIC_TOOLS:
                                    requested = (tool_args.get("vehicle_name") or "").strip().lower()
                                    if requested and requested not in assigned_set:
                                        _blocked = True
                                        result = {
                                            "error": (
                                                f"Access denied: you can only query your"
                                                f" assigned vehicle(s) ({', '.join(assigned_trucks)}),"
                                                f" not '{tool_args.get('vehicle_name')}'."
                                            ),
                                        }
                                if tool_name in ACCOUNT_WIDE_TOOLS:
                                    _blocked = True
                                    result = {
                                        "error": (
                                            f"Access denied: {tool_name} returns"
                                            f" fleet-wide data not available to drivers."
                                        ),
                                    }

                    if _blocked:
                        tool_results.append({"tool": tool_name, "args": tool_args, "data": result})
                        fn_response = Part.from_function_response(
                            name=tool_name,
                            response={"result": result},
                        )
                        response = await asyncio.to_thread(
                            model.generate_content,
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
                    response = await asyncio.to_thread(
                        model.generate_content,
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
                _capture_usage(response)
                if user_id is not None:
                    _store_history(user_id, question, text, account_id=account_id or 0)
                if ck and not has_history:
                    _cache_put(ck, text)
                return {"text": text, "tool_results": tool_results}

            text = await ask_ai(question, fleet_context, user_id=user_id,
                                   account_id=account_id, language=language,
                                   user_context=user_context)
            return {"text": text, "tool_results": tool_results}

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
            text = await ask_ai(question, fleet_context, user_id=user_id,
                                   account_id=account_id, language=language,
                                   user_context=user_context)
            return {"text": text, "tool_results": tool_results}

    logger.warning(f"Agent exhausted retries, falling back: {last_exc}")
    text = await ask_ai(question, fleet_context, user_id=user_id,
                           account_id=account_id, language=language,
                           user_context=user_context)
    return {"text": text, "tool_results": tool_results}


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
            from capabilities.ai.generation import get_last_usage
            clean, suggestions = _parse_sug(reply)
            await queue.put({
                "type": "done",
                "reply": clean,
                "suggestions": suggestions,
                "usage": get_last_usage(),
                "tool_results": result.get("tool_results", []),
            })
        except Exception as exc:
            await queue.put({"type": "error", "message": str(exc)})

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
