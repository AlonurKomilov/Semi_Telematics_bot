"""After a file reaches the cloud, its owning row must say so.

The sync worker uploads a queued file to Drive and then DELETES the local
copy.  That is only safe once the DB row points at the Drive id instead
of the vanished local path — otherwise the feature's reads 404 with no
error anywhere: the upload succeeded, the queue drained, the bytes are
safe, and the app simply cannot find them.

So every synced ``entity_type`` needs a repointer, and the worker fails
closed without one (see ``sync_worker._sync_one_row``).

WHY THESE ARE DERIVED, NOT HAND-WRITTEN
---------------------------------------
The column each feature stores its reference in is ALREADY declared, in
``capabilities.object_store.references`` — that is what stops the orphan scan
deleting live files.  Writing a second, independent list here would mean
two declarations of the same fact, free to drift: the scan would protect
one column while the worker repointed another, and the mismatch would
only show up as missing images long after the sync ran.

So the repointers are built FROM the registry.  A feature declares its
reference column once; correct orphan-protection and correct repointing
both follow.

THE TWO SHAPES
--------------
``simple``  one row, one column holds the whole reference.  Set it to
            the Drive id.
``json_slot`` one row, one JSON column holds MANY references keyed by
            slot (``driver_applications.docs_json`` maps cdlFront /
            cdlBack / medical / signature -> path).  The entity id alone
            cannot say which file was just uploaded, so the slot is found
            by matching the queue row's ``local_path`` value and only
            that slot is rewritten.  Replacing the blob wholesale would
            destroy the other three documents.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def simple_repointer(table: str, column: str):
    """``UPDATE <table> SET <column> = <drive_id> WHERE id = <entity_id>``.

    Identifiers are interpolated, so they must come from the registry —
    ``FileReference.__post_init__`` already rejects anything that isn't a
    plain identifier.
    """

    async def repoint(db, *, entity_id: int, drive_id: str, **_ctx) -> None:
        await db._db.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?",
            (drive_id, entity_id),
        )
        await db._db.commit()

    repoint.__name__ = f"repoint_{table}_{column}"
    return repoint


def json_slot_repointer(table: str, column: str):
    """Rewrite only the slot whose value is the file that just synced."""

    async def repoint(
        db, *, entity_id: int, drive_id: str, local_path: str = "", **_ctx,
    ) -> None:
        cur = await db._db.execute(
            f"SELECT {column} AS v FROM {table} WHERE id = ?", (entity_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise LookupError(f"{table} id={entity_id} not found for repoint")
        raw = dict(row).get("v") or "{}"
        try:
            doc = json.loads(raw)
        except (ValueError, TypeError) as e:
            # Raise: the worker keeps the local file and parks the row.
            # Silently skipping would delete the only readable copy.
            raise ValueError(f"{table}.{column} id={entity_id} is not JSON") from e
        if not isinstance(doc, dict):
            raise ValueError(f"{table}.{column} id={entity_id} is not a JSON object")

        hits = [k for k, v in doc.items() if isinstance(v, str) and v == local_path]
        if not hits:
            # Nothing to point at this file — repointing would be a
            # guess, and a wrong guess corrupts a different document.
            raise LookupError(
                f"{table}.{column} id={entity_id} has no slot holding "
                f"{local_path!r}; refusing to guess which one to rewrite"
            )
        for k in hits:
            doc[k] = drive_id
        await db._db.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?",
            (json.dumps(doc), entity_id),
        )
        await db._db.commit()

    repoint.__name__ = f"repoint_json_{table}_{column}"
    return repoint


# ── entity_type → the reference it repoints ─────────────────────────
#
# The KEY is the ``entity_type`` a writer passes to ``track_for_sync``;
# the VALUE names the registry entry that describes where that entity
# keeps its path.  Kept as (table, column) rather than a live import so
# a typo fails the drift test in tests/test_storage_reference_registry
# rather than at 3am in the worker.

ENTITY_REFERENCE: dict[str, tuple[str, str, str]] = {
    # entity_type            table                     column                kind
    "pti_media":            ("pti_inspection_media",   "file_path",          "simple"),
    "parking_map":          ("parking_events",         "map_image_path",     "simple"),
    "camera_check":         ("camera_checks",          "image_path",         "simple"),
    "work_order_attachment": ("work_order_attachments", "file_path",         "simple"),
    "maintenance_attachment": ("maintenance_tasks",    "attachment_path",    "simple"),
    "knowledge_media":      ("knowledge_base",         "media_url",          "simple"),
    "driver_document":      ("driver_documents",       "drive_file_id",      "simple"),
    "company_logo":         ("companies",              "logo_object_id",     "simple"),
    "company_banner":       ("companies",              "banner_object_id",   "simple"),
    "application_doc":      ("driver_applications",    "docs_json",          "json_slot"),
}

_BUILDERS = {"simple": simple_repointer, "json_slot": json_slot_repointer}


def build_all() -> dict:
    """Every registered entity_type → its repointer callable."""
    out = {}
    for entity_type, (table, column, kind) in ENTITY_REFERENCE.items():
        out[entity_type] = _BUILDERS[kind](table, column)
    return out


async def other_rows_reference(
    db, entity_type: str, *, entity_id: int, local_path: str,
) -> int:
    """How many OTHER rows still point at this local file.

    The worker deletes the local copy after repointing ONE row.  That is
    only safe when no other row references the same file — and on live
    data, plenty do:

        camera_checks.image_path    17,689 rows -> 340 files  (52x)
        parking_events.map_image_path 4,185 rows -> 338 files (12x)

    Both keyed their object-store entry by VEHICLE rather than by event,
    so one file served every check that truck ever had.  Parking's key is
    per-event now, but the 4,185 rows written before that fix still
    share.  Repointing one and deleting the file would 404 the other
    eleven, with no error anywhere.

    So this is checked at delete time, not just at enqueue time: it holds
    for legacy rows, for a writer wired up without noticing its key is
    shared, and for anything added later.  Returns 0 when the file is
    exclusively this row's.
    """
    spec = ENTITY_REFERENCE.get(entity_type)
    if spec is None or not local_path:
        return 0
    table, column, kind = spec
    if kind == "json_slot":
        # A JSON blob holds many paths; substring containment across rows
        # is not a reliable count, and applications write one folder per
        # application, so sharing is not a live risk here.
        return 0
    cur = await db._db.execute(
        f"SELECT count(*) AS n FROM {table} WHERE {column} = ? AND id <> ?",
        (local_path, entity_id),
    )
    row = await cur.fetchone()
    return int(dict(row).get("n") or 0) if row else 0
