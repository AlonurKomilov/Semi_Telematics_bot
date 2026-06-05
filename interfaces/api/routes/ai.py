"""AI Assistant API endpoints — chat, summary, diagnosis, model management."""


import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import capabilities.ai as ai
from capabilities.ai import _chat_histories
from capabilities.ai.registry import DEFAULT_LOCATION
from capabilities.ai.usage import build_user_ai_context, log_ai_usage as _log_ai_usage_fn, parse_ai_suggestions as _parse_suggestions
from capabilities.iam.permissions import is_management_role
from interfaces.api.deps import require_permission, require_permission_any, get_current_user, get_platform_db, get_tenant_db
from interfaces.api.rate_limit import limiter

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Error sanitisation ───────────────────────────────────────────
#
# Errors from Vertex AI / Anthropic / Mistral propagate up with raw
# URLs, bearer tokens, project IDs, and filesystem paths embedded in
# the message string.  Surfacing those verbatim over SSE leaks
# infrastructure details into browser devtools and any client-side log
# the user happens to share.  ``_safe_error_message`` returns a short
# class-name + redacted-summary suitable for client display; the full
# error still goes to server logs above.

# Match ``Authorization: Bearer xxx`` OR bare ``Bearer xxx`` — must
# replace the whole pair, else stripping just ``Authorization:`` leaves
# ``Bearer ya29.SECRET`` visible.
_TOKEN_RE = re.compile(
    r"(?:Authorization:\s+)?Bearer\s+\S+",
    re.IGNORECASE,
)
_PROJECT_RE = re.compile(r"projects/[^/\s\"']+")
_KEYFILE_RE = re.compile(r"/[\w\-./]+\.json")
_GCP_HOST_RE = re.compile(r"https?://[\w\-.]*googleapis\.com[^\s\"']*")


def _scrub_error_text(msg: str, max_len: int = 200) -> str:
    """Strip credentials / project IDs / GCP URLs / keyfile paths from *msg*."""
    msg = _TOKEN_RE.sub("Bearer [REDACTED]", msg)
    msg = _PROJECT_RE.sub("projects/[REDACTED]", msg)
    msg = _KEYFILE_RE.sub("[KEYFILE]", msg)
    msg = _GCP_HOST_RE.sub("[URL]", msg)
    msg = msg.replace("\n", " ").strip()
    if len(msg) > max_len:
        msg = msg[:max_len].rstrip() + "..."
    return msg


def _safe_error_message(exc: Exception, max_len: int = 200) -> str:
    """Return a short, redacted error message safe for client display."""
    name = type(exc).__name__
    raw = str(exc) or name
    msg = _scrub_error_text(raw, max_len=max_len)
    return f"{name}: {msg}" if msg != name else name


# ── Request / Response models ────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class DiagnoseRequest(BaseModel):
    vehicle_name: str
    dtcs: list[dict] = []
    lights: dict = {}


class ModelSwitchRequest(BaseModel):
    model_name: str
    model_type: str = Field("text", pattern=r"^(text|vision)$")


# ── Helpers ──────────────────────────────────────────────────────


async def _log_usage(account_id: int, user_id: int, action: str,
                     usage: dict | None,
                     *,
                     role: str | None = None,
                     latency_ms: int | None = None,
                     tool_success_count: int | None = None):
    """Delegates to the shared logger in capabilities.ai.usage.

    ``usage`` is the dict returned alongside each AI call's text
    result.  ``role`` + ``latency_ms`` + ``tool_success_count`` feed
    the router scorer so it can prefer the historically-best-for-role
    model on the next call's fallback chain.
    """
    from infra.services import get_platform_db as _gpdb
    pdb = _gpdb()  # synchronous — returns PlatformDB directly
    await _log_ai_usage_fn(
        ai, pdb, account_id, user_id, action, usage,
        role=role, latency_ms=latency_ms,
        tool_success_count=tool_success_count,
    )


async def _get_user_info(user: dict, platform_db) -> tuple[dict | None, list[str] | None, str]:
    """Fetch full user from DB and build user_context + vehicle_filter + language."""
    user_obj = await platform_db.get_user_by_telegram_id(
        int(user["sub"]),
    )
    if not user_obj:
        return None, None, "en"

    user_context = build_user_ai_context(user_obj)

    # Get all assigned trucks
    vehicle_nums = await platform_db.get_user_vehicle_nums(user_obj.id)
    if not vehicle_nums and user_obj.truck_num:
        vehicle_nums = [user_obj.truck_num]
    user_context["vehicle_nums"] = vehicle_nums or []
    if vehicle_nums:
        user_context["vehicle_num"] = vehicle_nums[0]

    vehicle_filter = vehicle_nums if user_context["role"] == "driver" and vehicle_nums else None
    language = getattr(user_obj, "language", "en") or "en"
    return user_context, vehicle_filter, language


# ── Chat ─────────────────────────────────────────────────────────

@router.post("/chat")
@limiter.limit("10/minute")
async def ai_chat(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(
        require_permission_any("can_ai_chat")
    ),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Send a message to the AI fleet assistant (agent mode with tools).

    Gated on any of (can_faults, can_vehicle_all, can_vehicle_own) to
    match the sidebar + React route — without this guard, a 403'd user
    could still call the endpoint directly and bypass the dashboard.
    """
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)

    try:
        import time as _t
        snapshot = await ai.build_context(
            account_id, vehicle_nums=vehicle_filter,
        )
        from infra.services import get_client
        samsara = await get_client(account_id)

        _started = _t.monotonic()
        result = await ai.ask_agent(
            body.message, snapshot,
            samsara_client=samsara,
            user_id=int(user["sub"]),
            account_id=account_id,
            db=tenant_db,
            language=language,
            user_context=user_context,
        )
        latency_ms = int((_t.monotonic() - _started) * 1000)
        answer = result["text"]
        usage = result.get("usage")
        # Count successful tool calls — feeds the router's tool_success_rate
        # signal so models that misuse tools get downranked for this role.
        tool_results = result.get("tool_results") or []
        tool_success_count = sum(
            1 for tr in tool_results
            if not (isinstance(tr.get("data"), dict) and tr["data"].get("error"))
        )
        await _log_usage(
            user["account_id"], int(user["sub"]), "question", usage,
            role=(user_context or {}).get("role"),
            latency_ms=latency_ms,
            tool_success_count=tool_success_count,
        )

        clean, suggestions = _parse_suggestions(answer)

        # Persist to DB using clean text so suggestions don't re-appear on reload
        try:
            await platform_db.save_chat_messages(
                account_id, int(user["sub"]), body.message, clean
            )
        except Exception:
            pass  # never block the chat reply on DB failure

        return {"reply": clean, "suggestions": suggestions, "usage": usage}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {type(e).__name__}")


# ── Chat (streaming SSE) ─────────────────────────────────────────

@router.post("/chat/stream")
@limiter.limit("10/minute")
async def ai_chat_stream(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(
        require_permission_any("can_ai_chat")
    ),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Send a message to the AI fleet assistant; streams SSE tool events then the reply.

    Gated identically to ``/chat`` (any of can_faults / can_vehicle_all
    / can_vehicle_own).  The streaming and non-streaming variants must
    stay aligned — clients fall back to ``/chat`` when SSE isn't
    available, so a permission mismatch would make the fallback work
    where the primary doesn't (or vice-versa).
    """
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)

    try:
        snapshot = await ai.build_context(account_id, vehicle_nums=vehicle_filter)
        from infra.services import get_client
        samsara = await get_client(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI context error: {type(e).__name__}")

    uid = int(user["sub"])

    async def _event_stream():
        try:
            async for event in ai.ask_agent_stream(
                body.message, snapshot,
                samsara_client=samsara,
                user_id=uid,
                account_id=account_id,
                db=tenant_db,
                language=language,
                user_context=user_context,
            ):
                # Upstream may emit ``{"type": "error", "message": str(exc)}``
                # with the raw exception text — scrub it before it leaves the
                # server so credentials / project IDs / GCP URLs / service-
                # account paths don't reach the browser devtools.
                if event.get("type") == "error":
                    msg = event.get("message", "")
                    event = {
                        **event,
                        "message": _scrub_error_text(msg),
                    }
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    # Persist the final reply to DB
                    reply = event.get("reply", "")
                    try:
                        await platform_db.save_chat_messages(account_id, uid, body.message, reply)
                    except Exception:
                        pass
                    # Log usage + router telemetry — read it off the
                    # ``done`` event (set by ask_agent_stream from the
                    # agent's return value).
                    try:
                        tool_results = event.get("tool_results") or []
                        tool_success_count = sum(
                            1 for tr in tool_results
                            if not (isinstance(tr.get("data"), dict)
                                    and tr["data"].get("error"))
                        )
                        await _log_usage(
                            account_id, uid, "question",
                            event.get("usage"),
                            role=(user_context or {}).get("role"),
                            tool_success_count=tool_success_count,
                        )
                    except Exception:
                        pass
        except Exception as exc:
            # Same scrub for exceptions raised *during* iteration.
            import logging
            logging.getLogger("api.ai").exception("SSE stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': _safe_error_message(exc)})}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


# ── Fleet Summary ────────────────────────────────────────────────

@router.post("/summary")
@limiter.limit("5/minute")
async def ai_summary(
    request: Request,
    user: dict = Depends(
        require_permission_any("can_ai_chat")
    ),
    platform_db=Depends(get_platform_db),
):
    """Generate an AI executive fleet briefing."""
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)

    try:
        snapshot = await ai.build_context(
            account_id, vehicle_nums=vehicle_filter,
        )
        # ``generate_summary`` passes action="summary" into generate() which
        # writes router telemetry per model attempt — no external log call
        # needed (it would double-log the same row).
        summary, usage = await ai.generate_summary(
            snapshot, account_id=account_id,
            language=language, user_context=user_context,
        )

        clean, suggestions = _parse_suggestions(summary)
        return {"summary": clean, "suggestions": suggestions, "usage": usage}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {type(e).__name__}")


# ── Fault Diagnosis ──────────────────────────────────────────────

@router.post("/diagnose")
@limiter.limit("5/minute")
async def ai_diagnose(
    body: DiagnoseRequest,
    request: Request,
    user: dict = Depends(require_permission("can_faults")),
    platform_db=Depends(get_platform_db),
):
    """AI-powered fault code diagnosis for a specific vehicle."""
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, _, language = await _get_user_info(user, platform_db)

    try:
        # ``diagnose_faults`` passes action="diagnosis" into generate()
        # which writes router telemetry per attempt — no external log
        # call here (would duplicate the row).
        diagnosis, _usage = await ai.diagnose_faults(
            body.vehicle_name,
            body.dtcs,
            lights=body.lights or None,
            account_id=account_id,
            language=language,
            user_context=user_context,
        )
        return {"diagnosis": diagnosis, "vehicle": body.vehicle_name}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {type(e).__name__}")


# ── Model Management ─────────────────────────────────────────────

@router.get("/models")
async def list_models(
    user: dict = Depends(get_current_user),
):
    """List available AI models with current selection."""
    account_id = user["account_id"]
    user_id = int(user["sub"])
    role = user.get("role", "")
    is_admin = is_management_role(role)

    # Load user model pref if not cached
    await ai.ensure_user_model(account_id, user_id)

    # User's active model: user pref → account default → global
    user_model = ai.get_user_model_name(user_id)
    account_model = ai.get_account_model_name(account_id) or ai.get_current_model_name()
    current_text = user_model or account_model
    current_vision = ai.get_account_vision_model_name(account_id) or ai.DEFAULT_VISION_MODEL

    models = []
    for name, info in ai.MODEL_REGISTRY.items():
        entry: dict = {
            "name": name,
            "display": info.get("display", name),
            "description": info.get("description", ""),
            "category": info.get("category", "unknown"),
            "maker": ai.get_model_maker(name),
            "vision": ai.is_vision_capable(name),
        }
        # Only show cost to admin/owner
        if is_admin:
            cost = ai.estimate_request_cost(name)
            entry["cost_per_request"] = round(cost, 4) if cost else None
        else:
            entry["cost_per_request"] = None
        models.append(entry)

    return {
        "models": models,
        "current_text": current_text,
        "current_vision": current_vision,
        "account_default": account_model,
        "is_admin": is_admin,
    }


@router.put("/model")
async def switch_model(
    body: ModelSwitchRequest,
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Switch the AI model for the account (owner/admin only)."""
    if body.model_name not in ai.MODEL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown model: {body.model_name}")

    account_id = user["account_id"]
    info = ai.MODEL_REGISTRY[body.model_name]
    locations = info.get("locations", [DEFAULT_LOCATION])
    location = locations[0] if locations else DEFAULT_LOCATION

    try:
        if body.model_type == "text":
            await ai.save_account_model(account_id, body.model_name, location)
            ai.switch_model(body.model_name, location)
        else:
            if not ai.is_vision_capable(body.model_name):
                raise HTTPException(
                    status_code=400,
                    detail=f"Model {body.model_name} is not vision-capable",
                )
            await ai.save_account_vision_model(account_id, body.model_name, location)
        return {"ok": True, "model": body.model_name, "type": body.model_type}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class UserModelRequest(BaseModel):
    model_name: str


@router.put("/user-model")
async def switch_user_model(
    body: UserModelRequest,
    user: dict = Depends(get_current_user),
):
    """Switch the AI model for the current user (any role)."""
    if body.model_name not in ai.MODEL_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown model: {body.model_name}")

    account_id = user["account_id"]
    user_id = int(user["sub"])
    info = ai.MODEL_REGISTRY[body.model_name]
    locations = info.get("locations", [DEFAULT_LOCATION])
    location = locations[0] if locations else DEFAULT_LOCATION

    try:
        await ai.save_user_model(account_id, user_id, body.model_name, location)
        ai.switch_user_model(user_id, body.model_name, location)
        return {"ok": True, "model": body.model_name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Chat History ─────────────────────────────────────────────────

@router.get("/history")
async def get_history(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Get the current conversation history.

    Reads straight from the DB so the dashboard sees the full
    scrollback window (``_MAX_ROWS_PER_USER`` rows), not just the
    smaller slice the in-memory ``_chat_histories`` cache holds for
    prompt-building.  The cache is still warmed here so the next AI
    call has prior context when chosen — but only with the recent
    slice, since the prompt has a token budget the UI doesn't.
    """
    from capabilities.ai.chat import _MAX_HISTORY

    uid = int(user["sub"])
    account_id = user["account_id"]

    db_rows: list[dict] = []
    try:
        db_rows = await platform_db.get_chat_history(account_id, uid)
    except Exception as e:
        # Real failure — surface it.  Frontend ``.catch`` now shows a
        # banner so the user knows their scrollback didn't load (vs.
        # the old silent ``catch`` that looked like "empty history").
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load history: {type(e).__name__}",
        )

    # Warm the in-memory cache with the recent slice used for prompt
    # context.  ``_store_history`` caps this at ``_MAX_HISTORY * 2``
    # rows on next write, so seeding with that same window keeps the
    # cache from oscillating in size.
    if db_rows and (uid, account_id) not in _chat_histories:
        recent = db_rows[-_MAX_HISTORY * 2:]
        _chat_histories[(uid, account_id)] = [
            {"role": ("User" if r["role"] == "user" else "Assistant"),
             "text": r["text"]}
            for r in recent
        ]

    def _norm_role(r: str) -> str:
        return "user" if r.lower() in ("user",) else "model"

    return {
        "messages": [
            {
                "role": _norm_role(r["role"]),
                "text": r["text"],
                # ISO-ish timestamp from the DB; frontend parses it
                # into a real Date for "12:34 PM" / "Jan 5" labels
                # instead of stamping every loaded message with the
                # browser's current time.
                "ts": r.get("created_at"),
            }
            for r in db_rows
        ],
        "count": len(db_rows),
    }


@router.delete("/history")
async def clear_history(
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Clear the conversation history."""
    uid = int(user["sub"])
    account_id = user["account_id"]
    ai.clear_history(uid, account_id=account_id)  # clear in-memory
    try:
        await platform_db.clear_chat_history(account_id, uid)
    except Exception:
        pass
    return {"ok": True}
