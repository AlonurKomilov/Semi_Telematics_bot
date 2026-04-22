"""AI Assistant API endpoints — chat, summary, diagnosis, model management."""


from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import capabilities.ai as ai
from capabilities.ai import _chat_histories
from capabilities.ai.registry import DEFAULT_LOCATION
from capabilities.ai.usage import build_user_ai_context, log_ai_usage as _log_ai_usage_fn, parse_ai_suggestions as _parse_suggestions
from capabilities.iam.permissions import is_management_role
from interfaces.api.deps import require_permission, get_current_user, get_platform_db, get_tenant_db
from interfaces.api.rate_limit import limiter

router = APIRouter(prefix="/ai", tags=["ai"])


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


async def _log_usage(account_id: int, user_id: int, action: str):
    """Delegates to the shared logger in capabilities.ai.usage."""
    from core.services import get_platform_db as _gpdb
    pdb = await _gpdb()
    await _log_ai_usage_fn(ai, pdb, account_id, user_id, action)


async def _get_user_info(user: dict, platform_db) -> tuple[dict | None, list[str] | None, str]:
    """Fetch full user from DB and build user_context + truck_filter + language."""
    user_obj = await platform_db.get_user_by_telegram_id(
        int(user["sub"]),
    )
    if not user_obj:
        return None, None, "en"

    user_context = build_user_ai_context(user_obj)

    # Get all assigned trucks
    truck_nums = await platform_db.get_user_truck_nums(user_obj.id)
    if not truck_nums and user_obj.truck_num:
        truck_nums = [user_obj.truck_num]
    user_context["truck_nums"] = truck_nums or []
    if truck_nums:
        user_context["truck_num"] = truck_nums[0]

    truck_filter = truck_nums if user_context["role"] == "driver" and truck_nums else None
    language = getattr(user_obj, "language", "en") or "en"
    return user_context, truck_filter, language


# ── Chat ─────────────────────────────────────────────────────────

@router.post("/chat")
@limiter.limit("10/minute")
async def ai_chat(
    body: ChatRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Send a message to the AI fleet assistant (agent mode with tools)."""
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, truck_filter, language = await _get_user_info(user, platform_db)

    try:
        snapshot = await ai.build_context(
            account_id, truck_nums=truck_filter,
        )
        from core.services import get_client
        samsara = await get_client(account_id)

        result = await ai.ask_agent(
            body.message, snapshot,
            samsara_client=samsara,
            user_id=int(user["sub"]),
            account_id=account_id,
            db=tenant_db,
            language=language,
            user_context=user_context,
        )
        answer = result["text"]
        await _log_usage(user["account_id"], int(user["sub"]), "question")

        clean, suggestions = _parse_suggestions(answer)
        return {"reply": clean, "suggestions": suggestions}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI error: {type(e).__name__}")


# ── Fleet Summary ────────────────────────────────────────────────

@router.post("/summary")
@limiter.limit("5/minute")
async def ai_summary(
    request: Request,
    user: dict = Depends(require_permission("can_faults")),
    platform_db=Depends(get_platform_db),
):
    """Generate an AI executive fleet briefing."""
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, truck_filter, language = await _get_user_info(user, platform_db)

    try:
        snapshot = await ai.build_context(
            account_id, truck_nums=truck_filter,
        )
        summary = await ai.generate_summary(
            snapshot, account_id=account_id,
            language=language, user_context=user_context,
        )
        await _log_usage(account_id, int(user["sub"]), "summary")

        clean, suggestions = _parse_suggestions(summary)
        return {"summary": clean, "suggestions": suggestions}

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
        diagnosis = await ai.diagnose_faults(
            body.vehicle_name,
            body.dtcs,
            lights=body.lights or None,
            account_id=account_id,
            language=language,
            user_context=user_context,
        )
        await _log_usage(account_id, int(user["sub"]), "diagnosis")
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
            "category": info.get("category", "unknown"),
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
):
    """Get the current conversation history."""
    uid = int(user["sub"])
    account_id = user["account_id"]
    history = _chat_histories.get((uid, account_id), [])
    return {
        "messages": [
            {"role": h["role"], "text": h["text"]}
            for h in history
        ],
        "count": len(history),
    }


@router.delete("/history")
async def clear_history(
    user: dict = Depends(get_current_user),
):
    """Clear the conversation history."""
    uid = int(user["sub"])
    ai.clear_history(uid, account_id=user["account_id"])  # sync function
    return {"ok": True}
