"""AI Assistant API endpoints — chat, summary, diagnosis, model management."""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): ONLY router.py may import interfaces.api.deps.



import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import capabilities.ai as ai
from capabilities.ai import _chat_histories
from capabilities.ai.registry import DEFAULT_LOCATION
from capabilities.ai.usage import build_user_ai_context, log_ai_usage as _log_ai_usage_fn, parse_ai_suggestions as _parse_suggestions
from capabilities.permissions.roles import is_management_role
from interfaces.api.deps import require_permission, require_permission_any, get_current_user, get_platform_db, get_tenant_db, active_view
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


def _scope_descriptor(user_context: dict | None) -> dict:
    """Describe the AI data scope applied this turn, for the trust badge.

    Reads ``scoped_vehicle_nums`` — the Team Management Vehicle Access SSOT
    set by :func:`_get_user_info`: ``None`` = unrestricted (All access);
    a list = restricted to exactly those vehicles (``[]`` = no access).  The
    frontend shows "Answering for your N vehicles" only when ``restricted``.
    """
    scoped = (user_context or {}).get("scoped_vehicle_nums")
    if scoped is None:
        return {"restricted": False}
    return {"restricted": True, "vehicle_count": len(scoped)}


def _apply_persona_preview(user_context: dict | None, view: str | None) -> None:
    """Owner/Admin "View as <role>" — gate the AI as the previewed role so the
    preview is faithful: the assistant advertises, allows, and refuses tools
    exactly as that role would.

    ``view`` comes from :func:`active_view`, which already enforces that ONLY
    owner/admin may preview another role and only ever returns an
    equal-or-narrower role (a non-switchable user always gets their real role
    back).  So this can never *escalate* access — it only relabels the role
    used for tool gating, narrowing it.  Vehicle-Access scope is deliberately
    left as the real user's: we preview *which tools* a role gets, not a
    specific other user's data scope.  No-op when not previewing.
    """
    if user_context and view and view != user_context.get("role"):
        user_context["role"] = view


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

    # Effective AI data scope from Vehicle Access (All / Company / Vehicle) —
    # the single source of truth in Team Management.  ``None`` = unrestricted;
    # a list = the only vehicles the AI may serve this user.  Drives both the
    # server-side tool gate (via user_context) and the prompt snapshot below.
    from capabilities.ai.scope import resolve_vehicle_scope
    scope = await resolve_vehicle_scope(
        platform_db, user_obj.account_id, user_obj.id,
        user_context.get("role"), truck_num=getattr(user_obj, "truck_num", None),
    )
    user_context["scoped_vehicle_nums"] = scope

    # Snapshot scoping mirrors the gate.  ``[]`` (restricted-to-none) must
    # still filter the snapshot to empty, so pass a non-matching sentinel
    # rather than None (which build_context reads as "all vehicles").
    if scope is None:
        vehicle_filter = None
    elif scope:
        vehicle_filter = scope
    else:
        vehicle_filter = ["\x00__no_access__"]
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
    view: str = Depends(active_view),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Send a message to the AI fleet assistant (agent mode with tools).

    Gated on any of (can_faults, can_vehicle_all, can_vehicle_vehicle) to
    match the sidebar + React route — without this guard, a 403'd user
    could still call the endpoint directly and bypass the dashboard.
    """
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)
    _apply_persona_preview(user_context, view)  # owner/admin "View as <role>"

    try:
        import time as _t
        # Implicit-satisfaction signals: re-ask within 30 s (timing)
        # + dissatisfaction phrase at message start ("no, that's not
        # what I asked", localised).  Both flip ``had_reask`` on the
        # *prior* turn's row so the scorer downweights that model on
        # future picks.  Run before tier resolution so the marker
        # targets the previous turn, not the one we're about to start.
        await ai.detect_reask_and_mark(account_id, int(user["sub"]))
        await ai.detect_phrase_dissatisfaction_and_mark(
            account_id, int(user["sub"]), body.message,
            language=language,
        )

        # Auto-mode tier resolution.  When the user's stored choice is
        # "auto" this classifies the prompt and hot-swaps the per-user
        # model cache to the right tier before ask_agent runs.  No-op
        # for explicit-tier users — returns the stored tier unchanged.
        await ai.resolve_tier_for_request(
            body.message,
            account_id=account_id, user_id=int(user["sub"]),
        )
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
        # ask_agent now emits per-attempt rows with action="question"
        # (one per model.generate_content() call inside the agent loop).
        # This external log uses a distinct ``"question_turn"`` action
        # so the per-attempt and per-turn signals stay in separate
        # scoring slices — preserves tool_success_count + cumulative
        # latency for observability without polluting the per-attempt
        # rows the router scores on.
        await _log_usage(
            user["account_id"], int(user["sub"]), "question_turn", usage,
            role=(user_context or {}).get("role"),
            latency_ms=latency_ms,
            tool_success_count=tool_success_count,
        )

        # Heuristic refusal detector.  When the AI refused a question
        # that a relevant tool could have answered (and didn't call),
        # flip had_reask on THIS turn's row (not the prior one).
        # Fires only on real refusals — legitimate "no tool exists
        # for this" answers stay clean.  Zero LLM cost; runs in
        # microseconds.
        await ai.detect_unjustified_refusal_and_mark(
            user["account_id"], int(user["sub"]),
            body.message, answer, tool_results,
            language=language,
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
    view: str = Depends(active_view),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Send a message to the AI fleet assistant; streams SSE tool events then the reply.

    Gated identically to ``/chat`` (any of can_faults / can_vehicle_all
    / can_vehicle_vehicle).  The streaming and non-streaming variants must
    stay aligned — clients fall back to ``/chat`` when SSE isn't
    available, so a permission mismatch would make the fallback work
    where the primary doesn't (or vice-versa).
    """
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)
    _apply_persona_preview(user_context, view)  # owner/admin "View as <role>"

    try:
        # Implicit-satisfaction markers (timing + phrase).  Same as
        # the non-streaming /chat path — both fire before tier
        # resolution so they target the prior turn's row.
        await ai.detect_reask_and_mark(account_id, int(user["sub"]))
        await ai.detect_phrase_dissatisfaction_and_mark(
            account_id, int(user["sub"]), body.message,
            language=language,
        )

        # Auto-mode tier resolution — same as the non-streaming /chat
        # path.  Has to fire before build_context so the model is
        # already staged when ask_agent_stream picks it up.
        await ai.resolve_tier_for_request(
            body.message,
            account_id=account_id, user_id=int(user["sub"]),
        )
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
                elif event.get("type") == "done":
                    # Attach the data-scope descriptor so the client can show a
                    # "Answering for your N vehicles" trust badge for restricted
                    # users (and nothing for unrestricted ones).
                    event = {**event, "scope": _scope_descriptor(user_context)}
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    # Persist the final reply to DB
                    reply = event.get("reply", "")
                    try:
                        await platform_db.save_chat_messages(account_id, uid, body.message, reply)
                    except Exception:
                        pass
                    # Log per-turn observability row.  Per-attempt rows
                    # (action="question") are emitted inside ask_agent's
                    # model loop; this row uses ``"question_turn"`` so
                    # the two streams stay in separate scoring slices.
                    try:
                        tool_results = event.get("tool_results") or []
                        tool_success_count = sum(
                            1 for tr in tool_results
                            if not (isinstance(tr.get("data"), dict)
                                    and tr["data"].get("error"))
                        )
                        await _log_usage(
                            account_id, uid, "question_turn",
                            event.get("usage"),
                            role=(user_context or {}).get("role"),
                            tool_success_count=tool_success_count,
                        )
                    except Exception:
                        pass

                    # Heuristic refusal detector — same as the non-
                    # streaming path.  Wrapped in try/except so a
                    # detector hiccup never breaks an otherwise-valid
                    # streaming response.
                    try:
                        await ai.detect_unjustified_refusal_and_mark(
                            account_id, uid,
                            body.message, reply, tool_results,
                            language=language,
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
            "tier": ai.get_model_tier(name),  # 'fast' / 'thinking' / 'reasoning' / None
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


# ── Tier Management ──────────────────────────────────────────────


class TierSwitchRequest(BaseModel):
    # ``auto`` is a valid stored choice — it picks a real tier per
    # request from the prompt category (see capabilities/ai/models.py
    # resolve_tier_for_request).
    tier: str = Field(..., pattern=r"^(fast|thinking|reasoning|auto)$")


@router.get("/tier")
async def get_tier(
    user: dict = Depends(get_current_user),
):
    """Return the user's current tier + all available tier choices.

    The current_tier field carries the user's *stored* choice —
    which may be ``"auto"``.  current_model is None when the stored
    choice is auto, because the actual model only gets picked at
    request time from the prompt.  Dashboards should show "Auto"
    selected without claiming a fixed underlying model.
    """
    account_id = user["account_id"]
    user_id = int(user["sub"])
    await ai.ensure_user_tier(account_id, user_id)

    current_tier = await ai.resolve_tier(account_id, user_id)
    if current_tier == ai.TIER_AUTO:
        current_model: str | None = None
        current_model_display: str | None = None
    else:
        current_model = await ai.pick_model_for_tier(current_tier)
        _info = ai.MODEL_REGISTRY.get(current_model, {})
        current_model_display = _info.get("display", current_model)

    return {
        "current_tier": current_tier,
        "current_model": current_model,
        "current_model_display": current_model_display,
        "tiers": [
            {
                "name": t,
                "label": ai.TIER_DISPLAY[t]["label"],
                "icon": ai.TIER_DISPLAY[t]["icon"],
                "description": ai.TIER_DISPLAY[t]["description"],
                # Auto has no fixed chain — show 0 so the dashboard
                # can hide the "N models" badge for the Auto row.
                "model_count": (
                    0 if t == ai.TIER_AUTO else len(ai.get_tier_chain(t))
                ),
            }
            for t in ai.TIER_CHOICES
        ],
    }


@router.put("/tier")
async def switch_tier(
    body: TierSwitchRequest,
    user: dict = Depends(get_current_user),
):
    """Switch the user's tier — persisted per-(account, user).

    ``auto`` switches to per-request prompt-classified routing; the
    resolved model is None in that case (it'll be picked on the next
    chat call from the prompt).
    """
    account_id = user["account_id"]
    user_id = int(user["sub"])
    try:
        model_name = await ai.switch_tier(
            body.tier, account_id=account_id, user_id=user_id,
        )
        if body.tier == ai.TIER_AUTO:
            return {
                "ok": True,
                "tier": body.tier,
                "resolved_model": None,
                "resolved_model_display": None,
            }
        info = ai.MODEL_REGISTRY.get(model_name, {})
        return {
            "ok": True,
            "tier": body.tier,
            "resolved_model": model_name,
            "resolved_model_display": info.get("display", model_name),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# ── Feedback signals ─────────────────────────────────────────────

@router.post("/feedback/regenerate")
@limiter.limit("30/minute")
async def feedback_regenerate(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Flag the user's most recent AI response as unsatisfying.

    Powers the Regenerate button on the chat UI — the click itself is
    an explicit dissatisfaction signal, complementary to the timing-
    based ``detect_reask_and_mark`` and the phrase-based
    ``detect_phrase_dissatisfaction_and_mark``.  All three converge
    on the same ``had_reask`` column and the same scorer slice, so
    the router penalises the bad model regardless of *how* the
    user told us the answer was bad.

    Rate-limited (30/min) to prevent abuse — no caller-supplied id
    or row reference, so this is purely "this user, latest answer".
    Returns ``{ok: true, marked: bool}`` — ``marked: false`` when
    there's no eligible row yet (first turn of the conversation),
    not an error.

    The actual re-running of the prompt is done client-side by
    calling /ai/chat or /ai/chat/stream with the prior user message
    — the backend doesn't need to know it's a regenerate.
    """
    account_id = user["account_id"]
    user_id = int(user["sub"])
    marked = await ai.flip_last_response_as_reask(account_id, user_id)
    return {"ok": True, "marked": marked}


_THUMBS_DOWN_REASONS = {
    "inaccurate", "off_topic", "incomplete", "hallucinated",
    "vague", "unjust_refusal", "other",
}


class ThumbsDownRequest(BaseModel):
    # Both fields optional — a bare thumbs-down (no body) is the
    # original behaviour; the form follow-up sends reason + note.
    reason: str | None = Field(
        None,
        pattern=r"^(inaccurate|off_topic|incomplete|hallucinated|vague|unjust_refusal|other)$",
    )
    note: str | None = Field(None, max_length=500)


@router.post("/feedback/thumbs-down")
@limiter.limit("30/minute")
async def feedback_thumbs_down(
    request: Request,
    body: ThumbsDownRequest | None = None,
    user: dict = Depends(get_current_user),
):
    """Flag the user's most recent AI response as unsatisfying — no retry.

    Two call shapes accepted:
    - Bare POST (no body) — flips ``had_reask=TRUE`` only, same as
      the regenerate click.  Fired on the initial thumbs-down click
      for immediate signal.
    - POST with body ``{reason, note}`` — flips had_reask AND
      records WHY the user disliked the answer.  Fired when the
      "Why?" follow-up form is submitted.  ``reason`` is a fixed-
      vocabulary category; ``note`` is optional free-text (≤500
      chars).  Both validated by the Pydantic model — invalid
      categories return 422.

    Idempotent on had_reask (re-running won't double-flip).  When a
    bare call has fired first and the reason form arrives later,
    the reason column gets updated without changing had_reask —
    that's the form-completion update.
    """
    account_id = user["account_id"]
    user_id = int(user["sub"])
    reason = body.reason if body else None
    note = body.note if body else None
    marked = await ai.flip_last_response_as_thumbs_down(
        account_id, user_id, reason=reason, note=note,
    )
    return {"ok": True, "marked": marked}


@router.post("/feedback/thumbs-up")
@limiter.limit("30/minute")
async def feedback_thumbs_up(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Flag the user's most recent AI response as great.

    The ONLY explicit positive signal in the satisfaction stack.
    Flips ``had_thumbs_up=TRUE`` on the latest ok question row;
    the scorer adds 0.5x the thumbs-up rate to satisfaction (capped
    at 1.0), so models with consistently great answers can earn
    upweighting that pure no-feedback can't deliver.

    Same rate limit (30/min) and same "latest row for this user"
    targeting semantics as the negative endpoints, so positive and
    negative signals key on the same per-attempt rows.
    """
    account_id = user["account_id"]
    user_id = int(user["sub"])
    marked = await ai.flip_last_response_as_thumbs_up(account_id, user_id)
    return {"ok": True, "marked": marked}
