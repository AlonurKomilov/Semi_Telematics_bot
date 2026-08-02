"""DECLARED object-store reference columns — the orphan scan's ground truth.

A file is "referenced" when some DB row points at it.  Getting that set
wrong in the safe direction wastes disk; getting it wrong in the unsafe
direction DELETES LIVE DATA, so this module exists to make the set
explicit instead of inferred.

WHY THIS REPLACED NAME-MATCHING
-------------------------------
``orphans.py`` used to discover reference columns by matching column
names against substrings (``file_path``, ``object_id``, ``_url``, …) and
called that "drift-resistant".  It was the opposite: a column whose name
nobody anticipated is silently absent from the referenced set, and every
file it points at becomes a deletion candidate.  Measured on live data
before this change:

    parking_events.map_image_path      4,185 rows   not matched
    camera_checks.image_path          17,689 rows   not matched
    driver_applications.docs_json          6 rows   not matchable at all

…which made 437 of 601 local files (95.5 MB) "orphans", including FMCSA
driver medical certificates, CDL scans, and the unsafe-parking evidence
the 1095-day retention window exists to preserve.  Only the 7-day grace
window was holding any of them back, and grace windows expire.

The lesson is not "add more patterns" — that is the mechanism that
failed.  A reference must be DECLARED, and a drift test must fail when a
new candidate column is neither declared nor explicitly dismissed.

EXTRACTION KINDS
----------------
``path``       the column value IS the stored path/URL (the common case).
``object_id``  the value is a backend-native id (a Drive file id).  Still
               registered: on ``hybrid`` the local copy may exist under a
               path while the row holds the remote id, and a file counted
               twice is harmless — a file counted zero times is not.
``json_paths`` the value is a JSON object/array whose LEAF STRINGS are
               paths (``driver_applications.docs_json``).  Whole-value
               matching can never work here: the scan compares normalized
               strings by exact set membership, so a blob holding five
               paths matched none of them.  This kind is why the fix
               could not have been another substring.

Name-pattern discovery is KEPT as a fallback union (see orphans.py) —
not because it works, but because it can only ever ADD to the referenced
set, and a larger referenced set means fewer deletions.  Belt and braces,
where the braces are the part being trusted.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

RefKind = Literal["path", "object_id", "json_paths"]

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class FileReference:
    """One column that can hold an object-store reference."""

    table: str
    column: str
    kind: RefKind
    feature: str          # who owns it — for the drift test's error message
    note: str = ""

    def __post_init__(self) -> None:
        if not (_IDENT.match(self.table) and _IDENT.match(self.column)):
            raise ValueError(
                f"unsafe identifier in FileReference({self.table}.{self.column}) "
                f"— these are interpolated into SQL"
            )


# ── The registry ────────────────────────────────────────────────────
#
# Ordered by feature so a reviewer can check a feature's writes against
# its reads.  Every feature that calls ``ObjectStore.put`` MUST appear
# here; ``tests/test_storage_reference_registry.py`` enforces it.

REFERENCES: tuple[FileReference, ...] = (
    # ── parking ─────────────────────────────────────────────────────
    FileReference(
        "parking_events", "map_image_path", "path", "parking",
        "AI location snapshot; per-event key since the overwrite fix",
    ),
    # ── cameras ─────────────────────────────────────────────────────
    FileReference(
        "camera_checks", "image_path", "path", "cameras",
        "dashcam still captured at the check",
    ),
    # ── driver applications (FMCSA intake) ──────────────────────────
    FileReference(
        "driver_applications", "docs_json", "json_paths", "applications",
        "CDL front/back, medical card, signature — a JSON map of slot->path",
    ),
    # ── inspections (PTI media) ─────────────────────────────────────
    FileReference(
        "pti_inspection_media", "file_path", "path", "inspections",
        "may hold a Drive id after sync flips storage_state to remote",
    ),
    FileReference(
        "pti_inspection_media", "local_path", "path", "inspections",
        "disk copy while storage_state is 'local' — the sync worker "
        "deletes this file on success, so a row here is the ONLY thing "
        "keeping a not-yet-uploaded blob out of the purge",
    ),
    FileReference(
        "pti_inspection_media", "remote_path", "object_id", "inspections",
    ),
    FileReference(
        "pti_inspection_items", "reference_image_url", "path", "inspections",
        "the 'what good looks like' photo attached to a checklist item",
    ),
    FileReference(
        "pti_checklist_template_items", "reference_image_url", "path",
        "inspections", "same, on the template the items are cloned from",
    ),
    # ── maintenance ─────────────────────────────────────────────────
    # Found by the drift test, not by hand: maintenance/router.py calls
    # store.put but nothing recorded where it lands, so every maintenance
    # attachment was a purge candidate the moment it aged past grace.
    FileReference(
        "maintenance_tasks", "attachment_path", "path", "maintenance",
    ),
    # ── work orders ─────────────────────────────────────────────────
    FileReference(
        "work_order_attachments", "file_path", "path", "work_orders",
    ),
    # ── knowledge base ──────────────────────────────────────────────
    # ``media_url``, not ``file_path`` — this table predates the
    # file_path convention and the old name-matcher caught it only by the
    # ``_url`` suffix rule, which is exactly the kind of accident this
    # registry replaces with a statement.
    FileReference(
        "knowledge_base", "media_url", "path", "knowledge",
    ),
    # ── driver documents ────────────────────────────────────────────
    FileReference(
        "driver_documents", "drive_file_id", "object_id", "drivers",
        "Drive id; the local counterpart lives under bucket/object_key",
    ),
    # ── company branding (recruiter apply-host) ─────────────────────
    FileReference(
        "companies", "logo_object_id", "object_id", "applications",
    ),
    FileReference(
        "companies", "banner_object_id", "object_id", "applications",
    ),
    FileReference(
        "driver_applications", "sig_object_id", "object_id", "applications",
        "consent signature stored separately from the docs_json map",
    ),
)


# Columns that LOOK like references to the drift test but are not, each
# with the reason.  Dismissing a column is a deliberate act that shows up
# in review — which is the whole point of the test.
NOT_REFERENCES: dict[str, str] = {
    "driver_documents.doc_type": "an enum (cdl / medical), not a path",
    "driver_documents.file_name": "display name only; the path is bucket+object_key",
    "knowledge_base.media_type": "a MIME type",
    "storage_sync_queue.local_path": (
        "the sync queue's own bookkeeping — but see orphans.py: a file "
        "AWAITING upload must never be reaped, so the queue is consulted "
        "separately rather than treated as a feature reference"
    ),
    "storage_sync_queue.filename": "queue bookkeeping (see local_path)",
    "driver_inspections.driver_signature": "inline signature payload, not a stored file",
    "driver_inspections.reviewer_signature": "inline signature payload, not a stored file",
    "scan_log.signature": "a request signature/hash, not a file",
    # ── external URLs — someone else's storage, never ours ──────────
    "safety_event_log.video_url": (
        "an EXTERNAL Samsara S3 URL (https://s3.samsara.com/...), 13,344 "
        "rows — the provider's storage, never a file on our disk"
    ),
    "carrier_profile.video_url": "an external marketing video link (YouTube etc.)",
    "billing_invoices.hosted_invoice_url": "a Stripe-hosted invoice page, not our file",
    "billing_invoices.invoice_pdf_url": "Stripe generates and hosts this PDF",
    # ── companions of a registered path column ──────────────────────
    "work_order_attachments.file_name": (
        "the object KEY, read as store.get(folder, file_name); the full "
        "reference is the sibling file_path column, already registered"
    ),
    "pti_inspection_media.file_name": "object key; file_path/local_path carry the reference",
    "pti_inspection_media.media_type": "a MIME type",
    "maintenance_tasks.attachment_name": "display name; attachment_path is the reference",
    "maintenance_tasks.attachment_content_type": "a MIME type",
}


def json_leaf_paths(raw: str) -> list[str]:
    """Every leaf string inside a JSON blob — the ``json_paths`` extractor.

    Returns [] for anything unparseable rather than raising: a malformed
    blob must not abort the scan, and contributing no references is the
    SAFE failure here only because the caller treats a failed column as a
    reason to skip deletion entirely (see orphans.py ``strict``).
    """
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


def declared_for_tables(existing_tables: set[str]) -> list[FileReference]:
    """Registered references whose table actually exists in this schema.

    A registered table missing from the DB is normal (a feature whose
    migration hasn't run on this deployment) and must not break the scan.
    """
    return [r for r in REFERENCES if r.table in existing_tables]
