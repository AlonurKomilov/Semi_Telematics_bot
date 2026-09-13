"""File a truck's paper from chat — propose → approve → upload.

Snap a cab card on a phone, drop it in the assistant, and the fields
that matter are read off it and offered for approval instead of typed.
The extraction engine is the same one the upload dialog's "Read dates
from file" button uses, so chat and form can never disagree about what
a document says.

Owner contract, mirroring project-ai-invoice-to-wo:
  * The AI never files anything.  It PROPOSES; a human approves; the
    client then uploads its device-held file to the vehicle the approve
    response names.  ``source_files`` are NAMES — the bytes never take
    a detour through an action payload.
  * Access = ``can_manage_vehicle_docs`` (TOOL_PERMISSIONS), scope =
    vehicle_param, so a scoped caller cannot file paperwork onto a
    truck they cannot open.
  * The vehicle must be IDENTIFIED, never guessed: a cab card filed
    against the wrong tractor is worse than none, because it reads as
    done.  The tool description tells the agent to ask.

Every model-supplied value is re-clamped here AND at execute time —
payloads outlive the code revision that wrote them.
"""

from __future__ import annotations

import logging

from features.vehicles.resolve import resolve_for_tool
from capabilities.ai.tools.registry import (
    register_action_executor,
    register_tool,
    tool_propose,
)
from adapters.storage.vehicle_documents import VEHICLE_DOC_TYPES
from features.vehicles.documents.extraction import _clean_date

logger = logging.getLogger(__name__)

_MAX_FILES = 5


def _normalize(args: dict) -> dict | None:
    """Model args → the only shape this action will act on, or None."""
    unit = str(args.get("vehicle_name") or "").strip()[:40]
    if not unit:
        return None
    doc_type = str(args.get("doc_type") or "").strip().lower().replace(
        " ", "_").replace("-", "_")
    if doc_type not in VEHICLE_DOC_TYPES:
        # Not a refusal — "other" is the honest catch-all, and a wrong
        # ENUM would 422 the upload after a confident approval.
        doc_type = "other"
    files = [str(n).strip()[:120]
             for n in (args.get("source_files") or [])[:_MAX_FILES]
             if str(n).strip()]
    return {
        "vehicle_name": unit,
        "doc_type": doc_type,
        # The company the model named, and the registry id the PROPOSE
        # step resolved. Both ride the payload so the executor acts on
        # the decision already made rather than remaking it — a unit
        # number alone cannot carry which of two companies' trucks the
        # human approved.
        "company": str(args.get("company") or "").strip().upper()[:20],
        "registry_id": args.get("registry_id"),
        # Dates go through the extractor's own validator: a US-format
        # or nonsense date becomes empty rather than a plausible wrong
        # one, for exactly the reason it does there.
        "issued_at": _clean_date(args.get("issued_at")),
        "expires_at": _clean_date(args.get("expires_at")),
        "source_files": files,
    }


@register_tool({
    "name": "file_vehicle_document",
    "description": (
        "Propose filing a vehicle document — registration, cab card, "
        "title, insurance, annual inspection, IFTA, permit, emissions "
        "— against a truck. Use when the user attaches a photo or PDF "
        "of a truck's paperwork: read the attachment first for the "
        "document type and its expiry date. This does NOT file it "
        "directly — the user approves, then the file uploads. If the "
        "document does not clearly identify the truck, ASK which "
        "vehicle it is — never guess, because a cab card filed against "
        "the wrong tractor reads as done. List the attached file names "
        "in source_files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "vehicle_name": {
                "type": "string",
                "description": "Unit number (e.g. '110'). Required — ask if the document does not say.",
            },
            "doc_type": {
                "type": "string",
                "description": (
                    "One of: registration, cab_card, title, insurance, "
                    "annual_inspection, ifta, permit, emissions, lease, "
                    "purchase, warranty, other."
                ),
            },
            "issued_at": {"type": "string", "description": "YYYY-MM-DD, if printed."},
            "expires_at": {
                "type": "string",
                "description": (
                    "YYYY-MM-DD the document stops being valid. This "
                    "drives the expiry warnings — omit it rather than "
                    "guessing."
                ),
            },
            "source_files": {
                "type": "array",
                "description": "Names of the attached files.",
                "items": {"type": "string"},
            },
        },
        "required": ["vehicle_name"],
    },
    # The approve endpoint reads writes/risk from HERE — the code
    # registry is the trust root, never the stored proposal row.  This
    # tool shipped without them: the executor below was registered, the
    # card rendered, and every Approve answered 400 "Not an executable
    # write action", so the feature was dead from its first day.  The
    # omission also made it invisible to the write-scope guard
    # (tests/test_ai_write_tool_scope.py filters on `writes`) and to
    # both write-suppression paths — one missing key, four gates
    # skipped.  `tests/test_ai_write_tool_contract.py` now pairs every
    # executor to its declaration so this cannot recur.
    "writes": True,
    "risk": "low",
    # Names ONE truck in `vehicle_name`, so the gate treats it as
    # vehicle-specific — the same shape as create_maintenance_task.
    "scope": "vehicle_param",
    # `vehicle_scope: "live"` makes the DISPATCHER refuse this for a
    # retired truck before the tool runs — you do not renew the
    # registration of a tractor you sold, and a fail-closed gate is the
    # only kind worth having on a write.  Reading a retired truck's
    # existing papers stays open; that is the archive's whole promise.
    "vehicle_scope": "live",
    "vehicle_arg": "vehicle_name",
})
async def file_vehicle_document_action(tool_args: dict, samsara_client,
                                       account_id: int | None = None,
                                       db=None) -> dict:
    norm = _normalize(tool_args or {})
    if norm is None:
        return {"error": "Which vehicle is this document for?"}

    # Ask the question BEFORE a human approves, not after.
    #
    # The propose step never resolved the truck, so an unknown unit
    # number — or one two companies share — was discovered only inside
    # the executor, after somebody had already clicked Approve on a card
    # that named a truck the system could not find. The "say which
    # company" question belongs at the point where the model can still
    # ask it.
    if db is not None and account_id is not None:
        _resolved, _err = await resolve_for_tool(
            db, account_id, {**(tool_args or {}),
                             "vehicle_name": norm["vehicle_name"]})
        if _err:
            return _err
        if _resolved is not None:
            # Carry the decision forward so the executor does not have
            # to make it a second time, and cannot make it differently.
            norm["registry_id"] = getattr(_resolved, "id", None)
            norm["company"] = (getattr(_resolved, "company_code", "") or "")

    label = norm["doc_type"].replace("_", " ")
    when = (f", expiring {norm['expires_at']}" if norm["expires_at"]
            else ", with no expiry date read from it")
    summary = (f"File a {label} on unit {norm['vehicle_name']}{when}.")
    return tool_propose(
        "file_vehicle_document", summary, norm,
        risk="low",
        consequence=(
            "Files the attached document on that truck. It appears on "
            "the vehicle's Documents card and in the DOT binder"
            + (", and warns you at 30, 14, 7 and 1 days before it "
               "lapses." if norm["expires_at"] else
               " — with no expiry date it will never warn you, so add "
               "one on the card if the document has one.")
        ),
    )


@register_action_executor("file_vehicle_document")
async def _execute_file_vehicle_document(payload, account_id, user_context, db):
    """Resolve the truck and hand the client somewhere to upload to.

    Deliberately creates NOTHING: unlike a work order there is no
    container to make first — the document IS the file, and the file is
    on the user's device.  This returns the registry id the client
    posts to, with the approved metadata, so the bytes travel one hop
    from the device to the upload endpoint that already enforces the
    size cap, the mime allow-list, the quota and the company wall.
    """
    norm = _normalize(payload or {})
    if norm is None:
        return {"created": False, "message": "No vehicle was identified."}

    # Re-resolve under the APPROVER's scope, through the shared resolver.
    #
    # This hand-rolled the twin check — correctly, but as a third copy —
    # and scanned the whole roster to find one unit. What it did NOT do
    # was re-apply the approving user's vehicle access, unlike its
    # sibling executors: a proposal made under one caller could be
    # approved by another who may not see that truck at all.
    ctx = user_context or {}
    scope_args: dict = {"vehicle_name": norm["vehicle_name"]}
    if norm.get("company"):
        scope_args["company"] = norm["company"]
    pinned_id = norm.get("registry_id")
    if ctx.get("scoped_vehicle_nums") is not None:
        scope_args["_scope_vehicles"] = list(ctx["scoped_vehicle_nums"])
        ladder = (ctx.get("scoped_vehicle_ladder") or {}).get("identities") or []
        if ladder:
            scope_args["_scope_identities"] = [list(i) for i in ladder]

    match, err = await resolve_for_tool(db, account_id, scope_args)
    if err:
        return {"created": False, "message": err.get("error", "")}
    if match is not None and pinned_id is not None and getattr(match, "id", None) != pinned_id:
        # The registry moved under the proposal — the number now names a
        # different truck than the one the human saw and approved.
        return {"created": False, "message": (
            f"Unit {norm['vehicle_name']} is not the truck this was "
            "approved for any more — ask again so the card names the "
            "current one."
        )}
    if match is None:
        return {"created": False,
                "message": f"No active truck numbered {norm['vehicle_name']}."}

    return {
        "created": True,
        "target_type": "vehicle_document",
        # The client uploads its device-held file HERE, by these names.
        "target_id": str(match.id),
        "upload_path": f"/vehicles/registry/{match.id}/documents",
        "vehicle_name": match.unit_number,
        "doc_type": norm["doc_type"],
        "issued_at": norm["issued_at"],
        "expires_at": norm["expires_at"],
        "source_files": norm["source_files"],
        "message": (
            f"Ready to file a {norm['doc_type'].replace('_', ' ')} on "
            f"{match.unit_number}."
        ),
    }
