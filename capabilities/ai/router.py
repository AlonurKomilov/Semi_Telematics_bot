"""AI Assistant API endpoints — chat, summary, diagnosis, model management."""
# router.py is interface-layer code co-located with its hub/domain
# (docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.



import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field, field_validator

import capabilities.ai as ai
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

class ChatAttachment(BaseModel):
    """A device-held file's text, riding inline for THIS turn only.

    The file itself stays on the user's device (browser storage); the
    server parses this transiently and persists nothing (see
    capabilities/ai/docs/ai-import-assistant.md).  Caps mirror the
    attachments module; the 2MB body middleware is the outer ceiling.
    """
    name: str = Field(..., min_length=1, max_length=120)
    content: str = Field(..., min_length=1, max_length=1_400_000)
    # "sheet" = spreadsheet text eligible for imports (gated);
    # "text"  = extracted document text (PDF/TXT), read-only lane;
    # "image" = device-compressed data-URL for vision-capable models.
    # Only selects the parser — a wrong value can't change privileges.
    kind: Literal["sheet", "text", "image"] = "sheet"

    @field_validator("content")
    @classmethod
    def _content_byte_cap(cls, v: str) -> str:
        # max_length above counts CHARS; the real ceiling (the 2MB body
        # middleware) counts BYTES — enforce the same byte cap as the
        # attachments module so non-Latin sheets get the friendly error,
        # not the blunt 413.
        from capabilities.ai.attachments import MAX_CONTENT_BYTES
        if len(v.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError(
                f"attachment too large (max {MAX_CONTENT_BYTES / 1_000_000:.1f} MB)"
            )
        return v


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    # Thread targeting (dashboard History panel).  ``conversation_id``
    # continues an existing thread; ``new_conversation`` forces a fresh
    # one (the "New chat" button).  Neither set → the user's latest
    # thread, created lazily on first-ever message (miniapp behaviour).
    conversation_id: int | None = None
    new_conversation: bool = False
    # Copilot page context — what the user is viewing (feature / filters /
    # selection / focused entity).  A PROMPT HINT only: it's folded into
    # the prompt so "this truck" / "these rows" resolve, but tools and
    # vehicle scope are still authorized from the JWT, never from this.
    # Same trust boundary as the X-View-As persona preview.
    page_context: dict | None = None
    # Transient CSV attachments (imports).  Streaming path only — the
    # non-streaming path suppresses writes, so it rejects these too.
    attachments: list[ChatAttachment] | None = Field(default=None, max_length=3)
    # Per-dashboard AI spaces: the persona subdomain this chat runs on
    # ('dash', 'fleet', …).  Partitions the user's OWN thread list —
    # never a security boundary (tools/scope stay JWT-gated).  Absent
    # (miniapp/bot) → unscoped legacy behavior.
    workspace: str | None = Field(default=None, min_length=2, max_length=20, pattern=r"^[a-z]+$")


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
        # Mark the lens so write actions are suppressed while previewing —
        # preview is a read-only view of another role, and a write proposed
        # under the narrowed role but executed under the real role would
        # muddy the audit trail (fable-advisor).  Enforced at the tool gate.
        user_context["preview_active"] = True


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
        db_user=user_obj,
    )
    user_context["scoped_vehicle_nums"] = scope
    from capabilities.ai.scope import resolve_scope_ladder
    user_context["scoped_vehicle_ladder"] = await resolve_scope_ladder(
        user_obj.account_id, scope,
    )

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

    Gated on ``can_ai_chat`` — a SERVICE flag, always on for every
    role (capabilities/permissions/taxonomy.py): the assistant answers
    only from data the caller's own feature grants already expose, so
    its access IS its features' access.  (This docstring described an
    older gate — can_view_faults / can_vehicle_* — for long enough that the
    verb migration's sweep is what noticed; the code has gated on
    can_ai_chat since the service split.)
    """
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)
    _apply_persona_preview(user_context, view)  # owner/admin "View as <role>"
    # Copilot page context — a prompt hint (see ChatRequest.page_context);
    # never an authorization input.
    if user_context is not None and body.page_context:
        user_context["page_context"] = body.page_context
    # Write actions require an approve UI + server-side proposal persistence,
    # both of which live only on the STREAMING path (dashboard).  This
    # non-streaming endpoint is used by the Telegram miniapp, which has
    # neither — so suppress write tools here to avoid proposing an action
    # the user could never approve.  (Reads are unaffected.)  Attachments
    # exist purely to feed imports (writes), so they're rejected here too.
    if body.attachments:
        raise HTTPException(
            status_code=400,
            detail="Attachments aren't supported on this surface — use the dashboard assistant.",
        )
    if user_context is not None:
        user_context["suppress_writes"] = True

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
        # model cache to the right tier before ask_agent runs.  For
        # explicit-tier users it re-stages the model when this worker's
        # cache is cold or from the wrong tier (post-restart / cross-
        # worker), so the DB-stored tier is always honoured.
        await ai.resolve_tier_for_request(
            body.message,
            account_id=account_id, user_id=int(user["sub"]),
        )
        # Resolve the thread BEFORE the context sync so the model's
        # memory is scoped to THIS conversation (a stale/foreign id
        # falls through to a fresh thread), and so the reply can carry
        # the id of a lazily-created thread back to the client.
        if body.new_conversation:
            conv_id = await platform_db.create_ai_conversation(
                account_id, int(user["sub"]), body.message, body.workspace,
            )
        else:
            conv_id = await platform_db.resolve_ai_conversation(
                account_id, int(user["sub"]), body.conversation_id, body.message,
                body.workspace,
            )
        # Conversational context comes from the DB, not this worker's
        # memory — a follow-up usually lands on a different gunicorn
        # worker than the previous turn, and without the sync the model
        # answers with no memory of it.
        await ai.sync_history_from_db(
            platform_db, account_id, int(user["sub"]), conversation_id=conv_id,
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
            from capabilities.ai.registry import get_model_tier_label
            _tier_label = ""
            try:
                _m = result.get("model") or ai.get_model_for_user(
                    int(user["sub"]), account_id)[1]
                _tier_label = get_model_tier_label(_m) or ""
            except Exception:
                pass
            await platform_db.save_chat_messages(
                account_id, int(user["sub"]), body.message, clean,
                conversation_id=conv_id,
                model_tier=_tier_label,
            )
        except Exception:
            pass  # never block the chat reply on DB failure

        return {
            "reply": clean, "suggestions": suggestions, "usage": usage,
            "conversation_id": conv_id,
        }

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

    Gated identically to ``/chat`` (``can_ai_chat``).  The streaming
    and non-streaming variants must
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
    # Copilot page context — same prompt hint as the non-streaming path.
    if user_context is not None and body.page_context:
        user_context["page_context"] = body.page_context
    # Transient attachments: parse the device-held file text into grids
    # (spreadsheets, import-gated) and text docs (PDF/TXT, read-only)
    # that live ONLY in this request's scope (nothing persists — see
    # capabilities/ai/docs/ai-import-assistant.md).
    if body.attachments:
        from capabilities.ai.attachments import (
            AttachmentError, parse_attachments_for_request,
        )
        if user_context is not None and user_context.get("preview_active"):
            raise HTTPException(
                status_code=400,
                detail="Attachments are disabled while previewing another role.",
            )
        try:
            grids, docs, images = await parse_attachments_for_request(
                body.attachments, user.get("role"), account_id,
            )
        except AttachmentError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if user_context is not None:
            if grids:
                user_context["_attachment_grids"] = grids
            if docs:
                user_context["_attachment_docs"] = docs
            if images:
                user_context["_attachment_images"] = images

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
        # Thread resolution + DB-backed conversational-context sync —
        # same as the non-streaming /chat path; see the comments there.
        if body.new_conversation:
            conv_id = await platform_db.create_ai_conversation(
                account_id, int(user["sub"]), body.message, body.workspace,
            )
        else:
            conv_id = await platform_db.resolve_ai_conversation(
                account_id, int(user["sub"]), body.conversation_id, body.message,
                body.workspace,
            )
        await ai.sync_history_from_db(
            platform_db, account_id, int(user["sub"]), conversation_id=conv_id,
        )
        snapshot = await ai.build_context(account_id, vehicle_nums=vehicle_filter)
        from infra.services import get_client
        samsara = await get_client(account_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI context error: {type(e).__name__}")

    uid = int(user["sub"])

    async def _event_stream():
        try:
            # First byte IMMEDIATELY: nginx's proxy_read_timeout counts
            # silence, and a turn that opens with quota retries/backoff
            # (or a slow context build) could emit nothing for 60s+ —
            # the mystery 504s.  The client ignores unknown event types,
            # so a ping is free insurance.
            yield 'data: {"type": "ping"}\n\n'
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
                    # Persist any write-action proposals the AI made this
                    # turn, injecting their server ids and STRIPPING the raw
                    # payload from the client copy (payload stays server-only
                    # — the client approves by id, never resends it).
                    _arts = event.get("artifacts") or []
                    for _i, _a in enumerate(_arts):
                        if (isinstance(_a, dict)
                                and _a.get("type") == "action_proposal"
                                and not _a.get("proposal_id")):
                            try:
                                _staged = _a.get("staged")
                                _pid = await platform_db.create_action_proposal(
                                    account_id, uid, _a.get("tool", ""),
                                    _a.get("summary", ""),
                                    json.dumps(_a.get("payload") or {}, default=str),
                                    risk=str(_a.get("risk", "low")),
                                    staged_payload_json=(
                                        json.dumps(_staged, default=str)
                                        if _staged is not None else ""
                                    ),
                                )
                                _a["proposal_id"] = _pid
                                # Link the nearest preceding preview to its
                                # proposal — powers the editable preview.
                                for _b in reversed(_arts[:_i]):
                                    if (isinstance(_b, dict)
                                            and _b.get("type") == "import_preview"
                                            and not _b.get("proposal_id")):
                                        _b["proposal_id"] = _pid
                                        break
                            except Exception:
                                _a["error"] = "Could not create the action."
                            # Server-only fields never reach the client —
                            # it approves by id, nothing else.
                            _a.pop("payload", None)
                            _a.pop("staged", None)
                    # Attach the data-scope descriptor so the client can show a
                    # "Answering for your N vehicles" trust badge for restricted
                    # users (and nothing for unrestricted ones) — plus the
                    # thread id so a lazily-created conversation is adopted
                    # by the client for its follow-up messages.
                    event = {
                        **event,
                        "scope": _scope_descriptor(user_context),
                        "conversation_id": conv_id,
                    }
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    # Persist the final reply to DB
                    reply = event.get("reply", "")
                    try:
                        # Text + tier label only — the chain-of-thought
                        # and process timeline stay browser-local (the
                        # client stores them from this same done event).
                        await platform_db.save_chat_messages(
                            account_id, uid, body.message, reply,
                            conversation_id=conv_id,
                            model_tier=event.get("model_tier") or "",
                        )
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
    view: str = Depends(active_view),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Generate an AI status briefing tailored to the caller's role.

    Honors the owner/admin "View as <role>" persona (like /chat) so the
    briefing matches the dashboard the user is looking at; the focus areas
    themselves resolve from the role's effective permissions inside
    ``generate_summary`` — nothing here is per-role.
    """
    if not ai.is_configured():
        raise HTTPException(status_code=503, detail="AI not configured")

    account_id = user["account_id"]
    await ai.ensure_account_model(account_id)
    user_context, vehicle_filter, language = await _get_user_info(user, platform_db)
    _apply_persona_preview(user_context, view)  # owner/admin "View as <role>"

    try:
        snapshot = await ai.build_context(
            account_id, vehicle_nums=vehicle_filter,
        )

        # Feature-keyed snapshot enrichment: roles whose permissions include
        # the hiring pipeline (recruiter, hr, or anyone granted the flag) get
        # application counts in their briefing data.  Keyed by the permission,
        # never the role name — best-effort, a failure never blocks the brief.
        try:
            from adapters.storage import Role as _Role
            from capabilities.permissions.roles import get_account_permissions
            _role_str = (user_context or {}).get("role")
            if _role_str:
                _perms = await get_account_permissions(_Role(_role_str), account_id)
                if getattr(_perms, "can_manage_applications", False):
                    _apps = await tenant_db.list_driver_applications(account_id, limit=500)
                    _by_stage: dict[str, int] = {}
                    for _a in _apps:
                        _s = str(_a.get("status") or "unknown")
                        _by_stage[_s] = _by_stage.get(_s, 0) + 1
                    snapshot["driver_applications"] = {
                        "total": len(_apps), "by_stage": _by_stage,
                    }
        except Exception:
            pass
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
    user: dict = Depends(require_permission("can_view_faults")),
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
    """Flat all-threads scrollback (legacy shape — the miniapp's view).

    The dashboard uses the /conversations endpoints instead.  Reads
    straight from the DB (``_MAX_ROWS_PER_USER`` rows).
    """
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

    # No cache-warm here anymore: the chat endpoints sync the model's
    # context from the DB per request, scoped to ONE thread — seeding
    # from this flat all-threads window would mix conversations.

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


# ── Conversations (the History panel's threads) ──────────────────

@router.get("/conversations")
async def list_conversations(
    workspace: str | None = None,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """The caller's chat threads, newest activity first.

    Strictly self-scoped — same access rule as /history: nobody reads
    another user's conversations through the customer API.
    ``workspace`` scopes the list to one dashboard's AI space ('dash'
    includes legacy unscoped threads); absent → all (legacy callers).
    """
    if workspace is not None and not (2 <= len(workspace) <= 20 and workspace.isalpha()):
        workspace = None
    try:
        conversations = await platform_db.list_ai_conversations(
            user["account_id"], int(user["sub"]), workspace,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load conversations: {type(e).__name__}",
        )
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """One thread's messages, oldest first — the History panel's open
    action.  A foreign/unknown id simply yields an empty list (the
    query is scoped to the caller), which the client renders as an
    empty chat rather than an error worth probing."""
    uid = int(user["sub"])
    account_id = user["account_id"]
    try:
        rows = await platform_db.get_chat_history(
            account_id, uid, conversation_id=conversation_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load conversation: {type(e).__name__}",
        )

    def _norm_role(r: str) -> str:
        return "user" if r.lower() in ("user",) else "model"

    return {
        "conversation_id": conversation_id,
        "messages": [
            {"role": _norm_role(r["role"]), "text": r["text"],
             "ts": r.get("created_at"),
             # Tier label only — the thought log lives in the browser's
             # localStorage (the client re-attaches it by message key).
             "model_tier": r.get("model_tier") or ""}
            for r in rows
        ],
        "count": len(rows),
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Delete ONE thread (per-chat trash icon).  Scoped delete — a
    foreign id deletes nothing and reports ok=False."""
    uid = int(user["sub"])
    account_id = user["account_id"]
    try:
        deleted = await platform_db.delete_ai_conversation(
            account_id, uid, conversation_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete conversation: {type(e).__name__}",
        )
    # Drop this worker's in-memory context if it was following the
    # deleted thread; the per-request DB sync heals every other worker.
    ai.clear_history(uid, account_id=account_id)
    return {"ok": deleted}


# ── Write actions ("hands") — approve / reject / status ──────────
#
# The AI proposes a write during a chat turn (persisted server-side);
# these endpoints are where the human decides.  approve/reject/status
# deliberately gate on the REAL JWT role only — persona preview
# (X-View-As) is a read-only lens and is never honored for writes.

@router.post("/actions/{proposal_id}/approve")
@limiter.limit("30/minute")
async def approve_action(
    proposal_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Execute an AI-proposed write after re-authorizing it server-side.

    Re-runs the tool permission gate on the user's real role, atomically
    claims the proposal (no double-execution), executes, and audits.
    Idempotent — a re-approve of a consumed proposal returns the stored
    result.  NOTE: no ``active_view`` dependency — writes never run under
    a persona preview.
    """
    user_context, _vf, _lang = await _get_user_info(user, platform_db)
    # Deliberately NOT applying persona preview here.
    from capabilities.ai.actions import execute_approved_action
    return await execute_approved_action(
        proposal_id, user=user, user_context=user_context,
        platform_db=platform_db, tenant_db=tenant_db,
    )


@router.post("/actions/{proposal_id}/undo")
@limiter.limit("30/minute")
async def undo_action(
    proposal_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
    tenant_db=Depends(get_tenant_db),
):
    """Reverse an EXECUTED action via its registered undo recipe.

    Copilot-style change-set undo (soft + evented + audited as
    ``ai_undo:<tool>``), allowed to the approver or an owner/admin,
    within the 7-day window.  No body, like approve; and like approve,
    no ``active_view`` dependency — undo never runs under a persona
    preview.
    """
    user_context, _vf, _lang = await _get_user_info(user, platform_db)
    from capabilities.ai.actions import undo_approved_action
    return await undo_approved_action(
        proposal_id, user=user, user_context=user_context,
        platform_db=platform_db, tenant_db=tenant_db,
    )


class RowEditBody(BaseModel):
    row: int = Field(..., ge=0, le=100_000)
    changes: dict = Field(default_factory=dict)


class RowRemoveBody(BaseModel):
    row: int = Field(..., ge=0, le=100_000)


@router.get("/actions/{proposal_id}/rows")
async def action_rows(
    proposal_id: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """The FULL staged rows of a pending import (the editable preview) —
    creator-only; server-side keys stripped."""
    from capabilities.ai.actions import get_staged_rows
    return await get_staged_rows(proposal_id, user=user, platform_db=platform_db)


@router.post("/actions/{proposal_id}/rows/edit")
@limiter.limit("60/minute")
async def action_row_edit(
    proposal_id: str,
    body: RowEditBody,
    request: Request,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Correct ONE cell (or a few fields) of one staged row before
    approving.  Validated by the target's own rules — fixed vocabularies
    are refused with the reason, never coerced; the server's staged rows
    are the single source the no-body approve executes."""
    from capabilities.ai.actions import edit_staged_row
    return await edit_staged_row(
        proposal_id, row_index=body.row, changes=body.changes,
        user=user, platform_db=platform_db,
    )


@router.post("/actions/{proposal_id}/rows/remove")
@limiter.limit("60/minute")
async def action_row_remove(
    proposal_id: str,
    body: RowRemoveBody,
    request: Request,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Drop one staged row from a pending import."""
    from capabilities.ai.actions import remove_staged_row
    return await remove_staged_row(
        proposal_id, row_index=body.row, user=user, platform_db=platform_db,
    )


@router.post("/actions/{proposal_id}/reject")
@limiter.limit("30/minute")
async def reject_action(
    proposal_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Decline a pending proposal — no write.  Records the refusal so the
    audit trail shows the action was offered and rejected."""
    ok = await platform_db.decline_action_proposal(
        proposal_id, user["account_id"], int(user["sub"]),
    )
    return {"ok": ok}


@router.get("/actions/{proposal_id}")
async def get_action(
    proposal_id: str,
    user: dict = Depends(get_current_user),
    platform_db=Depends(get_platform_db),
):
    """Current status of a proposal (so a refreshed card shows
    'already approved').  Never returns the raw payload — only the
    display fields + the result once consumed."""
    prop = await platform_db.get_action_proposal(
        proposal_id, user["account_id"], int(user["sub"]),
    )
    if prop is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    from capabilities.ai.actions import _client_result, undoable
    result = None
    undo_result = None
    if prop["status"] in ("consumed", "undone") and prop.get("result"):
        try:
            raw = json.loads(prop["result"])
            undo_result = raw.get("_undo") if isinstance(raw, dict) else None
            result = _client_result(raw)
        except (TypeError, ValueError):
            result = None
    return {
        "id": prop["id"], "tool": prop["tool"],
        "summary": prop["summary"], "risk": prop["risk"],
        "status": prop["status"], "result": result,
        # Copilot-style undo affordance for the card.
        "undoable": undoable(prop),
        "undo_result": undo_result,
    }


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
