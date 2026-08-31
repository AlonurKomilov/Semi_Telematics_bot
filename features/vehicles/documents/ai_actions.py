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

    unit = norm["vehicle_name"].strip().lower()
    match = None
    for v in await db.list_vehicles(account_id):
        if v.is_active and (v.unit_number or "").strip().lower() == unit:
            # A unit number is a reusable LABEL; two live trucks can
            # share one across companies.  Ambiguity is a question, not
            # a coin toss.
            if match is not None:
                return {
                    "created": False,
                    "message": (
                        f"More than one active truck is numbered "
                        f"{norm['vehicle_name']} — say which company."
                    ),
                }
            match = v
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
