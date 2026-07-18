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

from capabilities.ai.tools import get_tool_schema, get_action_executor, get_undo_executor

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
        return {"status": "consumed", "result": _client_result(_load(prop.get("result")))}
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
    # Bulk actions (imports): the server-derived rows the user approved,
    # staged un-truncated on the proposal.  Ride the context like the
    # other server-side channels (_db pattern) — executor signatures
    # stay stable.
    staged_raw = prop.get("staged_payload") or ""
    if staged_raw:
        try:
            exec_context["_staged_rows"] = json.loads(staged_raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=409,
                detail="This proposal's staged data is unreadable — ask again.",
            )

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
            return {"status": "consumed", "result": _client_result(_load(again.get("result")))}
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

    return {"status": "consumed", "result": _client_result(result)}


# Undo availability window — matches the proposal row's retention sweep
# (prune_ai_action_proposals days=7), stated explicitly so a lagging
# prune can't silently extend it.
UNDO_WINDOW_DAYS = 7


def undoable(prop: dict) -> bool:
    """Whether this proposal can be undone RIGHT NOW: executed, has a
    registered recipe, and within the window."""
    if prop.get("status") != "consumed":
        return False
    if get_undo_executor(prop.get("tool", "")) is None:
        return False
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=UNDO_WINDOW_DAYS)).isoformat()
    return str(prop.get("created_at") or "") > cutoff


async def undo_approved_action(
    proposal_id: str,
    *,
    user: dict,
    user_context: dict | None,
    platform_db,
    tenant_db,
) -> dict:
    """Reverse an EXECUTED proposal via its registered undo recipe.

    Copilot-style change-set undo, never a point-in-time restore: the
    recipe receives the stored result (the exact change-set, e.g.
    ``_item_ids``) and reverses only that — concurrent work by other
    users is untouched.

    Authorization (real JWT role — persona preview is never honored):
    the APPROVER may undo their own action; owner/admin may undo any
    employee's.  Both must still hold the tool's own permission.
    Atomic claim (consumed → undoing) prevents a double-undo; failure
    reverts to consumed so the undo stays available.  Audited as
    ``ai_undo:<tool>`` with the undoer as actor.
    """
    account_id = user["account_id"]
    uid = int(user["sub"])
    role = str(user.get("role") or "")

    prop = await platform_db.get_action_proposal_for_account(proposal_id, account_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    is_approver = int(prop.get("user_id") or 0) == uid
    if not is_approver and role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only the approver or an owner/admin can undo this action",
        )

    if prop["status"] == "undone":
        return {"status": "undone", "result": _load(prop.get("result")).get("_undo")}
    if not undoable(prop):
        raise HTTPException(
            status_code=409,
            detail="This action can't be undone (not executed, no undo "
                   "support, or outside the undo window)",
        )

    tool = prop["tool"]
    payload = _load(prop.get("payload"))
    result = _load(prop.get("result"))

    # Tool-permission re-check on the REAL role (same gate as approve).
    from capabilities.ai.intelligence import _check_tool_permission
    exec_context = {**(user_context or {}), "user_id": uid}
    blocked = await _check_tool_permission(
        tool, payload, user.get("role"), exec_context, account_id,
    )
    if blocked is not None:
        raise HTTPException(status_code=403, detail="You don't have permission for this action")

    recipe = get_undo_executor(tool)
    if recipe is None:   # raced a deploy that dropped the recipe
        raise HTTPException(status_code=409, detail="This action type has no undo")

    if not await platform_db.claim_action_undo(proposal_id, account_id):
        # Lost the race — report the winner's outcome if it finished.
        again = await platform_db.get_action_proposal_for_account(proposal_id, account_id)
        if again and again["status"] == "undone":
            return {"status": "undone", "result": _load(again.get("result")).get("_undo")}
        raise HTTPException(status_code=409, detail="Undo already in progress")

    try:
        undo_result = await recipe(result, payload, account_id, exec_context, tenant_db)
    except HTTPException:
        await platform_db.finalize_action_undo(proposal_id, account_id, success=False)
        raise
    except Exception as e:
        await platform_db.finalize_action_undo(proposal_id, account_id, success=False)
        logger.exception("AI undo recipe failed: %s", tool)
        raise HTTPException(status_code=500, detail=f"Undo failed: {type(e).__name__}") from e

    # ONE guarded UPDATE: status + who/when + the merged undo outcome —
    # no window where the card could see 'undone' without its message,
    # and no crash-gap that loses the outcome (reviewer W1/W2).
    await platform_db.finalize_action_undo(
        proposal_id, account_id, success=True, undone_by=uid,
        result_json=json.dumps({**result, "_undo": undo_result}, default=str),
    )

    try:
        await tenant_db.add_audit_log(
            account_id, uid,
            action=f"ai_undo:{tool}",
            target_type=str(result.get("target_type", "")) if isinstance(result, dict) else "",
            target_id=str(result.get("target_id", "")) if isinstance(result, dict) else "",
            details=json.dumps(
                {"proposal_id": proposal_id, "undo": undo_result,
                 "approved_by": prop.get("user_id")}, default=str)[:2000],
        )
    except Exception:
        logger.exception("Audit write failed for AI undo %s (undone anyway)", tool)

    return {"status": "undone", "result": undo_result}


# ── Editable import preview (per-cell corrections pre-approve) ──────
#
# The human-approval step gains hands: the proposal CREATOR may correct
# individual cells (or drop rows) of a PENDING import's staged rows.
# Edits are validated by the target's own edit_row hook — same rules as
# the import (fixed vocab refused with the reason, open vocab
# normalized, references re-resolved) — and mutate the SERVER's staged
# payload, so the no-body approve endpoint keeps executing exactly what
# the preview shows.  Approve/undo trust model unchanged.

def _staged_target(prop: dict):
    """The proposal's ImportTarget (via its payload), or None."""
    from capabilities.ai.attachments import get_import_target
    payload = _load(prop.get("payload"))
    return get_import_target(str(payload.get("target") or ""))


async def _load_editable(proposal_id: str, user: dict, platform_db):
    """Common loader for the rows endpoints: creator-scoped, pending,
    an import with an edit hook.  Returns (prop, target, rows)."""
    account_id = user["account_id"]
    uid = int(user["sub"])
    prop = await platform_db.get_action_proposal(proposal_id, account_id, uid)
    if prop is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if prop["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Rows can only be edited while pending (now {prop['status']})",
        )
    target = _staged_target(prop)
    if target is None or target.edit_row is None:
        raise HTTPException(status_code=400, detail="This action has no editable rows")
    try:
        rows = json.loads(prop.get("staged_payload") or "[]")
    except (TypeError, ValueError):
        raise HTTPException(status_code=409, detail="Staged data is unreadable")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=409, detail="No staged rows to edit")
    return prop, target, rows


def _client_rows(rows: list) -> list[dict]:
    return [
        {k: v for k, v in r.items() if not str(k).startswith("_")}
        for r in rows if isinstance(r, dict)
    ]


def _summary_for(target, n: int, prop: dict) -> str:
    skipped = _load(prop.get("payload")).get("skipped")
    summary = f"Import {n} {target.description or 'rows into ' + target.name}"
    if skipped:
        summary += f" — {skipped} skipped"
    return summary


async def get_staged_rows(proposal_id: str, *, user: dict, platform_db) -> dict:
    _prop, target, rows = await _load_editable(proposal_id, user, platform_db)
    return {
        "rows": _client_rows(rows),
        "to_import": len(rows),
        "edit_options": dict(target.edit_options),
    }


async def edit_staged_row(
    proposal_id: str, *, row_index: int, changes: dict,
    user: dict, platform_db,
) -> dict:
    prop, target, rows = await _load_editable(proposal_id, user, platform_db)
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(status_code=400, detail="No changes given")
    if not (0 <= row_index < len(rows)):
        raise HTTPException(status_code=404, detail="Row not found")
    new_row, error = await target.edit_row(
        rows[row_index], changes, user["account_id"], _tenant_of(user),
    )
    if new_row is None:
        raise HTTPException(status_code=422, detail=error or "Invalid edit")
    rows[row_index] = new_row
    await _persist_rows(proposal_id, user, platform_db, target, prop, rows)
    return {
        "row_index": row_index,
        "row": _client_rows([new_row])[0],
        "to_import": len(rows),
    }


async def remove_staged_row(
    proposal_id: str, *, row_index: int, user: dict, platform_db,
) -> dict:
    prop, target, rows = await _load_editable(proposal_id, user, platform_db)
    if not (0 <= row_index < len(rows)):
        raise HTTPException(status_code=404, detail="Row not found")
    if len(rows) == 1:
        raise HTTPException(
            status_code=422,
            detail="The last row can't be removed — reject the action instead",
        )
    rows.pop(row_index)
    await _persist_rows(proposal_id, user, platform_db, target, prop, rows)
    return {"rows": _client_rows(rows), "to_import": len(rows)}


async def _persist_rows(proposal_id, user, platform_db, target, prop, rows):
    ok = await platform_db.update_staged_payload(
        proposal_id, user["account_id"], int(user["sub"]),
        json.dumps(rows, default=str),
        summary=_summary_for(target, len(rows), prop),
    )
    if not ok:   # approved/declined between load and write
        raise HTTPException(status_code=409, detail="Proposal is no longer pending")


def _tenant_of(user):
    """The tenant db for edit_row's re-resolution — the rows endpoints
    receive only platform_db; resolution reads (vehicle registry) go
    through the shared service accessor like other request-path reads."""
    from infra.services import get_db
    return get_db()


def _client_result(result):
    """Strip server-side keys (underscore-prefixed, e.g. the ``_item_ids``
    undo manifest) from a result before it reaches the client."""
    if not isinstance(result, dict):
        return result
    return {k: v for k, v in result.items() if not k.startswith("_")}


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
