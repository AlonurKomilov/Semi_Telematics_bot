"""Vehicle documents — files that belong to one truck.

The registration, title, insurance and annual-inspection paperwork a
carrier keeps per unit.  Mirrors the driver-documents mixin because it
IS the same product idea for a different entity: rows point at objects
in the company's folder tree (``{COMPANY}/vehicles/{unit}/`` — see
capabilities/object_storage/docs/LAYOUT.md), the archive flow moves the
folder and rewrites ``bucket`` here, and quota accounting bumps the
same per-account counter.

Keyed by REGISTRY id.  Documents belong to the truck, and the registry
row is the truck — a telematics ref names a device that can move
between trucks, and filing a title under one would hand the paperwork
to whichever truck inherits the gateway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class."""
        _db: Any

        def transaction(self) -> Any: ...
        def _now(self) -> str: ...
else:
    _MixinBase = object

logger = logging.getLogger(__name__)

#: What a vehicle document can be.  ``other`` is the honest catch-all —
#: a closed list with no escape teaches people to mislabel.
VEHICLE_DOC_TYPES = (
    # The papers a US carrier is actually asked for at a scale house or
    # in an audit.  `cab_card` (IRP), `ifta`, `permit` and `emissions`
    # were missing while `lease` and `purchase` — the two nobody is
    # ever asked to produce roadside — were present, so the list read
    # as an office filing cabinet rather than what rides in the truck.
    "registration", "cab_card", "title", "insurance",
    "annual_inspection", "ifta", "permit", "emissions",
    "lease", "purchase", "warranty", "other",
)


@dataclass(frozen=True)
class VehicleDocument:
    id: int
    account_id: int
    vehicle_id: int
    doc_type: str
    bucket: str
    object_key: str
    drive_file_id: Optional[str]
    file_name: str
    file_size: Optional[int]
    mime_type: Optional[str]
    issued_at: Optional[str]
    expires_at: Optional[str]
    status: str
    uploaded_by: Optional[int]
    uploaded_at: str
    notes: Optional[str]


_COLS = (
    "id, account_id, vehicle_id, doc_type, bucket, object_key, "
    "drive_file_id, file_name, file_size, mime_type, issued_at, "
    "expires_at, status, uploaded_by, uploaded_at, notes"
)


def _row(r) -> VehicleDocument:
    return VehicleDocument(*r)


class VehicleDocumentsMixin(_MixinBase):

    async def add_vehicle_document(
        self,
        account_id: int,
        vehicle_id: int,
        *,
        doc_type: str,
        bucket: str,
        object_key: str,
        file_name: str = "",
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        drive_file_id: Optional[str] = None,
        issued_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        uploaded_by: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> VehicleDocument:
        if doc_type not in VEHICLE_DOC_TYPES:
            raise ValueError(f"Unknown doc_type: {doc_type}")
        now = self._now()
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT INTO vehicle_documents "
                "(account_id, vehicle_id, doc_type, bucket, object_key, "
                " drive_file_id, file_name, file_size, mime_type, "
                " issued_at, expires_at, status, uploaded_by, "
                " uploaded_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        'active', ?, ?, ?)",
                (account_id, vehicle_id, doc_type, bucket, object_key,
                 drive_file_id, file_name, file_size, mime_type,
                 issued_at, expires_at, uploaded_by, now, notes),
            )
            new_id = cur.lastrowid
            # Same per-account counter the driver docs bump — one quota,
            # not one per entity (meaningful for the local-disk backend;
            # Drive accounts cap on Google's side).
            if file_size:
                await self._db.execute(
                    "UPDATE accounts "
                    "SET storage_used_bytes = storage_used_bytes + ? "
                    "WHERE id = ?",
                    (file_size, account_id),
                )
        return VehicleDocument(
            id=new_id, account_id=account_id, vehicle_id=vehicle_id,
            doc_type=doc_type, bucket=bucket, object_key=object_key,
            drive_file_id=drive_file_id, file_name=file_name,
            file_size=file_size, mime_type=mime_type,
            issued_at=issued_at, expires_at=expires_at,
            status="active", uploaded_by=uploaded_by,
            uploaded_at=now, notes=notes,
        )

    async def list_vehicle_documents(
        self, account_id: int, vehicle_id: int,
    ) -> list[VehicleDocument]:
        cur = await self._db.execute(
            f"SELECT {_COLS} FROM vehicle_documents "
            "WHERE account_id = ? AND vehicle_id = ? AND status = 'active' "
            "ORDER BY uploaded_at DESC, id DESC",
            (account_id, vehicle_id),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def get_vehicle_document(
        self, account_id: int, doc_id: int,
    ) -> VehicleDocument | None:
        cur = await self._db.execute(
            f"SELECT {_COLS} FROM vehicle_documents "
            "WHERE id = ? AND account_id = ?",
            (doc_id, account_id),
        )
        r = await cur.fetchone()
        return _row(r) if r else None

    async def delete_vehicle_document(
        self, account_id: int, doc_id: int,
    ) -> VehicleDocument | None:
        """Soft delete — the row records what existed; the caller
        removes the object.  Returns the row so the caller has the
        bucket/key to remove, or None when nothing matched."""
        doc = await self.get_vehicle_document(account_id, doc_id)
        if doc is None or doc.status != "active":
            return None
        async with self.transaction():
            await self._db.execute(
                "UPDATE vehicle_documents SET status = 'deleted' "
                "WHERE id = ? AND account_id = ?",
                (doc_id, account_id),
            )
            if doc.file_size:
                await self._db.execute(
                    "UPDATE accounts "
                    "SET storage_used_bytes = "
                    "    GREATEST(storage_used_bytes - ?, 0) "
                    "WHERE id = ?",
                    (doc.file_size, account_id),
                )
        return doc

    async def get_expiring_vehicle_documents(
        self, account_id: int, *, within_days: int = 30,
    ) -> list[dict]:
        """Active documents on LIVE trucks whose expiry falls inside the
        window (past-due included, so the expired bucket can fire).

        Joined to ``vehicles`` and filtered there rather than in the
        caller: an archived truck raises no alerts of any kind — that is
        what archiving means — and a document alert is an alert.  The
        unit number and company ride along so the alert can name the
        truck without a second query.
        """
        cur = await self._db.execute(
            "SELECT d.id, d.vehicle_id, d.doc_type, d.file_name, "
            "       d.expires_at, v.unit_number, v.company_code "
            "  FROM vehicle_documents d "
            "  JOIN vehicles v ON v.id = d.vehicle_id "
            " WHERE d.account_id = ? AND d.status = 'active' "
            "   AND d.expires_at IS NOT NULL AND d.expires_at != '' "
            "   AND v.is_active = 1 "
            "   AND COALESCE(v.archived_reason, '') = '' "
            " ORDER BY d.expires_at ASC",
            (account_id,),
        )
        rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "id": r[0], "vehicle_id": r[1], "doc_type": r[2],
                "file_name": r[3], "expires_at": r[4],
                "unit_number": r[5], "company_code": r[6],
            })
        return out

    async def list_account_vehicle_documents(
        self, account_id: int,
    ) -> list[dict]:
        """Every active document across the account's LIVE trucks.

        The fleet-wide question a per-truck card cannot answer: which
        papers expire this month, and which truck do I go to.  Same
        join as the expiry query, without the window — the unit number
        and company ride along so the page names the truck without a
        second lookup.

        Live trucks only.  A retired truck's paperwork stays reachable
        from its own page (that is what archiving promises), but
        counting it here would inflate every compliance figure with
        trucks the carrier no longer runs.
        """
        cur = await self._db.execute(
            "SELECT d.id, d.vehicle_id, d.doc_type, d.file_name, "
            "       d.file_size, d.mime_type, d.issued_at, d.expires_at, "
            "       d.uploaded_at, d.notes, "
            "       v.unit_number, v.company_code, v.vehicle_type "
            "  FROM vehicle_documents d "
            "  JOIN vehicles v ON v.id = d.vehicle_id "
            " WHERE d.account_id = ? AND d.status = 'active' "
            "   AND v.is_active = 1 "
            "   AND COALESCE(v.archived_reason, '') = '' "
            " ORDER BY v.unit_number ASC, d.uploaded_at DESC",
            (account_id,),
        )
        rows = await cur.fetchall()
        keys = ("id", "vehicle_id", "doc_type", "file_name", "file_size",
                "mime_type", "issued_at", "expires_at", "uploaded_at",
                "notes", "unit_number", "company_code", "vehicle_type")
        return [dict(zip(keys, r)) for r in rows]

    async def record_vehicle_doc_notification(
        self, doc_id: int, bucket_days: int,
    ) -> bool:
        """Claim the T-``bucket_days`` alert for this document.

        True = newly inserted, so the caller sends.  False = a row
        already exists, so somebody already sent it.  Claimed BEFORE
        sending: a duplicate alert is noise, a missed one is a truck
        running on expired paper — but re-running a scheduler tick must
        not re-notify, and the composite PK settles it at the DB even
        with concurrent schedulers.
        """
        now = self._now()
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT OR IGNORE INTO vehicle_document_notifications "
                "(doc_id, bucket_days, notified_at) VALUES (?, ?, ?)",
                (doc_id, bucket_days, now),
            )
        return (cur.rowcount or 0) > 0

    async def move_vehicle_documents_bucket(
        self, account_id: int, vehicle_id: int,
        old_bucket: str, new_bucket: str,
    ) -> int:
        """Rewrite ``bucket`` after the physical folder moved — the
        archive/restore flow, same shape as the driver one: the
        ObjectStorage moves the folder, this keeps reads resolving."""
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE vehicle_documents SET bucket = ? "
                "WHERE account_id = ? AND vehicle_id = ? AND bucket = ?",
                (new_bucket, account_id, vehicle_id, old_bucket),
            )
        return cur.rowcount or 0
