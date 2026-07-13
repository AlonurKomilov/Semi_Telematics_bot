"""AI write-action execution — the approve-side of the copilot "hands".

The AI proposes; this module is where an approved proposal actually
executes, behind every server-side gate. Called only from the
``POST /ai/actions/{id}/approve`` endpoint.

Security spine (fable-advisor reviewed):
  * **Registry is the trust root.** ``writes`` / ``risk`` / the executor
    come from the code registry (``get_tool_schema`` / ``get_action_executor``),
    NEVER from the stored proposal row. A row can be data; the registry
    can't be forged.
  * **Real role only.** Re-authorized with ``_check_tool_permission`` on
    the user's REAL JWT role — persona preview (X-View-As) is a read-only
    lens and is never honored here.
  * **Atomic claim.** ``claim_action_proposal`` flips pending→executing
    in one conditional UPDATE, so concurrent approves can't double-write.
  * **Stale payload never trusted for authz.** The executor re-resolves
    its target inside ``account_id``; the payload is propose-time data.
  * **Audited.** Every executed write lands in ``audit_log`` (actor = the
    approving user, action = ``ai_write:<tool>``).
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException

from capabilities.ai.tools import get_tool_schema, get_action_executor

logger = logging.getLogger(__name__)

# High-risk write actions (delete / money / identity) are NOT enabled in
# the 4.0 launch — the low-risk create/acknowledge path ships first, each
# high-risk action gets its own review + stronger confirm before flipping
# this on.  The gate reads ``risk`` from the code registry, so a tampered
# proposal row can't smuggle a high-risk action through as "low".
HIGH_RISK_WRITES_ENABLED = False


async def execute_approved_action(
    proposal_id: str,
    *,
    user: dict,
    user_context: dict | None,
    platform_db,
    tenant_db,
) -> dict:
    """Execute an approved proposal, or raise HTTPException.

    Returns ``{"status": "consumed", "result": <executor result>}``.
    Idempotent: a second approve of a consumed proposal returns the
    stored result; a concurrent double-approve loses the claim race and
    also returns the stored result.
    """
    account_id = user["account_id"]
    uid = int(user["sub"])

    prop = await platform_db.get_action_proposal(proposal_id, account_id, uid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Already-resolved proposals return idempotently / refuse.
    if prop["status"] == "consumed":
        return {"status": "consumed", "result": _load(prop.get("result"))}
    if prop["status"] != "pending":
        # executing (a claim is in flight) / declined / failed.
        raise HTTPException(status_code=409, detail=f"Proposal is {prop['status']}")

    tool = prop["tool"]

    # ── Trust-root gate: writes/risk/executor from the CODE registry ──
    schema = get_tool_schema(tool)
    if not schema or not schema.get("writes"):
        raise HTTPException(status_code=400, detail="Not an executable write action")
    risk = str(schema.get("risk", "low"))
    if risk == "high" and not HIGH_RISK_WRITES_ENABLED:
        raise HTTPException(status_code=403, detail="This action type isn't enabled yet")
    executor = get_action_executor(tool)
    if executor is None:
        raise HTTPException(status_code=400, detail="No executor for this action")

    payload = _load(prop.get("payload"))

    # The executor stamps domain-level attribution (created_by /
    # acknowledged_by) off this context — inject the approving user's REAL
    # id (the JWT subject) so AI-driven writes attribute to the human who
    # approved them, not the 0/"auto-resolved" sentinel.  Never trust an
    # id that rode in from the client.
    exec_context = {**(user_context or {}), "user_id": uid}

    # ── Re-authorize on the REAL role (never the preview) ──
    from capabilities.ai.intelligence import _check_tool_permission
    blocked = await _check_tool_permission(
        tool, payload, user.get("role"), exec_context, account_id,
    )
    if blocked is not None:
        raise HTTPException(status_code=403, detail="You don't have permission for this action")

    # ── Atomic claim (pending → executing) — no double-execution ──
    if not await platform_db.claim_action_proposal(proposal_id, account_id, uid):
        # Lost the race or it just expired — return the winner's result
        # if it finished, else 409.
        again = await platform_db.get_action_proposal(proposal_id, account_id, uid)
        if again and again["status"] == "consumed":
            return {"status": "consumed", "result": _load(again.get("result"))}
        raise HTTPException(status_code=409, detail="Proposal already being handled")

    # ── Execute ──
    try:
        result = await executor(payload, account_id, exec_context, tenant_db)
    except HTTPException:
        await platform_db.finalize_action_proposal(proposal_id, account_id, uid, "failed")
        raise
    except Exception as e:
        await platform_db.finalize_action_proposal(proposal_id, account_id, uid, "failed")
        logger.exception("AI action executor failed: %s", tool)
        raise HTTPException(status_code=500, detail=f"Action failed: {type(e).__name__}") from e

    await platform_db.finalize_action_proposal(
        proposal_id, account_id, uid, "consumed",
        json.dumps(result, default=str),
    )

    # ── Audit (tenant-scoped log; actor = the approving user) ──
    try:
        await tenant_db.add_audit_log(
            account_id, uid,
            action=f"ai_write:{tool}",
            target_type=str(result.get("target_type", "")) if isinstance(result, dict) else "",
            target_id=str(result.get("target_id", "")) if isinstance(result, dict) else "",
            details=json.dumps({"proposal_id": proposal_id, "payload": payload}, default=str)[:2000],
        )
    except Exception:
        logger.exception("Audit write failed for AI action %s (executed anyway)", tool)

    return {"status": "consumed", "result": result}


def _load(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}
