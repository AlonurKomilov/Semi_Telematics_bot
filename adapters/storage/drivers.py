"""Driver-profile + vehicle-assignment + document-store mixins.

Drivers stay a special role within the ``users`` table — they are
not a separate entity.  These mixins read/write the driver-specific
columns on ``users`` plus the two new tables introduced by the
driver-module migrations:

  * ``driver_vehicle_assignments`` — single source of truth for who
    drives what, with history preserved via ``unassigned_at``.
  * ``driver_documents``           — per-driver document store with
    expiration tracking; files live in the existing ``ObjectStore``
    (Google Drive when the account has it linked, local disk
    fallback otherwise).

The local-disk fallback enforces a per-account quota
(``accounts.storage_quota_bytes``) so a single tenant can't fill
the volume on the host.  Google-Drive-connected accounts use their
own Drive quota and are uncapped here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any

        def acquire(self) -> Any: ...
        def transaction(self) -> Any: ...
        async def read_all(self, sql: str, params: tuple = ()) -> list: ...
        async def read_one(self, sql: str, params: tuple = ()) -> Any: ...
else:
    _MixinBase = object

logger = logging.getLogger(__name__)


# ── Dataclasses ────────────────────────────────────────────────


@dataclass
class DriverProfile:
    """The driver-specific subset of a ``users`` row."""
    user_id: int
    account_id: int
    display_name: str
    telegram_id: Optional[int]
    samsara_driver_id: Optional[str]
    # CDL
    cdl_number: Optional[str]
    cdl_state: Optional[str]
    cdl_class: Optional[str]
    cdl_expires: Optional[str]
    # Medical
    med_card_expires: Optional[str]
    # Employment
    hire_date: Optional[str]
    termination_date: Optional[str]
    # Contact
    dob: Optional[str]
    phone: Optional[str]
    home_address: Optional[str]
    driver_notes: Optional[str]


@dataclass
class VehicleAssignment:
    """One row from driver_vehicle_assignments — preserves history."""
    id: int
    account_id: int
    user_id: int
    vehicle_name: str
    vehicle_id: Optional[str]
    is_primary: bool
    assigned_by: Optional[int]
    assigned_at: str
    unassigned_at: Optional[str]
    notes: Optional[str]


@dataclass
class DriverDocument:
    """One row from driver_documents — addresses an object in the ObjectStore."""
    id: int
    account_id: int
    user_id: int
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


# Allowed driver-profile columns the API may update.  Kept as an
# explicit set so an admin route can't poke at unrelated user fields
# like ``email`` or ``password_hash`` by accident.
_DRIVER_PROFILE_COLUMNS = frozenset({
    "cdl_number", "cdl_state", "cdl_class", "cdl_expires",
    "med_card_expires", "hire_date", "termination_date",
    "dob", "phone", "home_address", "driver_notes",
    "samsara_driver_id",
})

# Doc-type whitelist for the writer API.  Open-ended on read (legacy
# rows survive) but writes go through this set.
VALID_DOC_TYPES = frozenset({
    "cdl", "medical_card", "mvr", "drug_screen",
    "background_check", "training_cert", "other",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Profile mixin ──────────────────────────────────────────────


class DriverProfileMixin(_MixinBase):
    """Driver-profile read/write on the ``users`` table."""

    async def get_driver_profile(self, user_id: int) -> Optional[DriverProfile]:
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, display_name, telegram_id, "
                "       samsara_driver_id, cdl_number, cdl_state, cdl_class, "
                "       cdl_expires, med_card_expires, hire_date, "
                "       termination_date, dob, phone, home_address, "
                "       driver_notes "
                "FROM users WHERE id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return DriverProfile(
            user_id=row[0],
            account_id=row[1],
            display_name=row[2] or "",
            telegram_id=row[3],
            samsara_driver_id=row[4],
            cdl_number=row[5],
            cdl_state=row[6],
            cdl_class=row[7],
            cdl_expires=row[8],
            med_card_expires=row[9],
            hire_date=row[10],
            termination_date=row[11],
            dob=row[12],
            phone=row[13],
            home_address=row[14],
            driver_notes=row[15],
        )

    async def update_driver_profile(
        self, user_id: int, **fields,
    ) -> bool:
        """Update one or more profile fields.  Silently drops keys that
        aren't in the allow-list (defensive against API callers
        passing through arbitrary kwargs)."""
        clean = {k: v for k, v in fields.items() if k in _DRIVER_PROFILE_COLUMNS}
        if not clean:
            return False
        cols = ", ".join(f"{k} = ?" for k in clean)
        params = tuple(clean.values()) + (user_id,)
        async with self.transaction():
            cur = await self._db.execute(
                f"UPDATE users SET {cols} WHERE id = ?",
                params,
            )
        return cur.rowcount > 0

    async def list_drivers(
        self, account_id: int, include_terminated: bool = False,
    ) -> list[DriverProfile]:
        """All drivers in an account.  Excludes terminated by default."""
        where = "WHERE account_id = ? AND role = 'driver' AND is_active = 1"
        if not include_terminated:
            where += " AND (termination_date IS NULL OR termination_date = '')"
        async with self.acquire() as conn:
            cur = await conn.execute(
                f"SELECT id, account_id, display_name, telegram_id, "
                f"       samsara_driver_id, cdl_number, cdl_state, cdl_class, "
                f"       cdl_expires, med_card_expires, hire_date, "
                f"       termination_date, dob, phone, home_address, "
                f"       driver_notes "
                f"FROM users {where} "
                f"ORDER BY LOWER(display_name)",
                (account_id,),
            )
            rows = await cur.fetchall()
        return [
            DriverProfile(
                user_id=r[0], account_id=r[1], display_name=r[2] or "",
                telegram_id=r[3], samsara_driver_id=r[4],
                cdl_number=r[5], cdl_state=r[6], cdl_class=r[7], cdl_expires=r[8],
                med_card_expires=r[9], hire_date=r[10], termination_date=r[11],
                dob=r[12], phone=r[13], home_address=r[14], driver_notes=r[15],
            )
            for r in rows
        ]


# ── Vehicle-assignment mixin (history-preserving) ──────────────


class DriverVehicleAssignmentsMixin(_MixinBase):
    """Single source of truth for driver↔vehicle mapping.

    Replaces the legacy ``driver_trucks`` table.  All writes go
    through here; reads in alerts/scorecards/etc. fall back to
    ``driver_trucks`` and ``users.truck_num`` during the transition
    period (handled by ``get_user_vehicle_nums``).
    """

    async def assign_vehicle_to_driver(
        self,
        account_id: int,
        user_id: int,
        vehicle_name: str,
        vehicle_id: Optional[str] = None,
        is_primary: bool = False,
        assigned_by: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> VehicleAssignment:
        """Create an active assignment.  When ``is_primary=True`` the
        existing primary (if any) is demoted to secondary in the
        same transaction so there's always at most one primary."""
        now = _now_iso()
        vehicle_name = vehicle_name.strip()
        async with self.transaction():
            if is_primary:
                await self._db.execute(
                    "UPDATE driver_vehicle_assignments "
                    "SET is_primary = 0 "
                    "WHERE user_id = ? AND unassigned_at IS NULL",
                    (user_id,),
                )
            cur = await self._db.execute(
                "INSERT INTO driver_vehicle_assignments "
                "(account_id, user_id, vehicle_name, vehicle_id, "
                " is_primary, assigned_by, assigned_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_id, user_id, vehicle_name, vehicle_id,
                 int(is_primary), assigned_by, now, notes),
            )
            new_id = cur.lastrowid
            # Sync the denormalized cache on users.truck_num so legacy
            # readers (alert pipeline driver-filter, AI agent) stay
            # consistent with the new source of truth.  Only on primary.
            if is_primary:
                await self._db.execute(
                    "UPDATE users SET truck_num = ? WHERE id = ?",
                    (vehicle_name, user_id),
                )
        logger.info(
            "Vehicle assigned to driver: user=%d vehicle=%s primary=%s",
            user_id, vehicle_name, is_primary,
        )
        return VehicleAssignment(
            id=new_id, account_id=account_id, user_id=user_id,
            vehicle_name=vehicle_name, vehicle_id=vehicle_id,
            is_primary=is_primary, assigned_by=assigned_by,
            assigned_at=now, unassigned_at=None, notes=notes,
        )

    async def end_vehicle_assignment(
        self, assignment_id: int, *, by: Optional[int] = None,
    ) -> bool:
        """Mark an active assignment as unassigned.  Preserves the
        row for history.  Returns True if a row was updated.

        Named ``end_vehicle_assignment`` rather than
        ``unassign_vehicle`` to avoid colliding with the legacy
        ``DriverVehiclesMixin.unassign_vehicle(user_id, truck_num)``
        method, which has different semantics (deletes the
        ``driver_trucks`` row outright rather than preserving
        history).  The new method works on the new
        ``driver_vehicle_assignments`` table only."""
        now = _now_iso()
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE driver_vehicle_assignments "
                "SET unassigned_at = ? "
                "WHERE id = ? AND unassigned_at IS NULL",
                (now, assignment_id),
            )
            updated = cur.rowcount > 0
            if updated:
                # If the unassigned row was the primary, clear the
                # denormalized cache on users.truck_num.  A new
                # primary (if any) overwrites it on the next assign.
                await self._db.execute(
                    "UPDATE users SET truck_num = NULL "
                    "WHERE id IN ("
                    "    SELECT user_id FROM driver_vehicle_assignments "
                    "    WHERE id = ? AND is_primary = 1"
                    ")",
                    (assignment_id,),
                )
        if updated and by is not None:
            logger.info("Vehicle unassignment: assignment_id=%d by=%d", assignment_id, by)
        return updated

    async def list_active_assignments(self, user_id: int) -> list[VehicleAssignment]:
        """All currently-active assignments for a driver, primary first."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, vehicle_name, vehicle_id, "
                "       is_primary, assigned_by, assigned_at, "
                "       unassigned_at, notes "
                "FROM driver_vehicle_assignments "
                "WHERE user_id = ? AND unassigned_at IS NULL "
                "ORDER BY is_primary DESC, vehicle_name",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [self._row_to_assignment(r) for r in rows]

    async def list_assignment_history(self, user_id: int) -> list[VehicleAssignment]:
        """Active + historical assignments for a driver (newest first)."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, vehicle_name, vehicle_id, "
                "       is_primary, assigned_by, assigned_at, "
                "       unassigned_at, notes "
                "FROM driver_vehicle_assignments "
                "WHERE user_id = ? "
                "ORDER BY assigned_at DESC",
                (user_id,),
            )
            rows = await cur.fetchall()
        return [self._row_to_assignment(r) for r in rows]

    @staticmethod
    def _row_to_assignment(r) -> VehicleAssignment:
        return VehicleAssignment(
            id=r[0], account_id=r[1], user_id=r[2],
            vehicle_name=r[3], vehicle_id=r[4],
            is_primary=bool(r[5]), assigned_by=r[6],
            assigned_at=r[7], unassigned_at=r[8], notes=r[9],
        )


# ── Document store + storage quota ─────────────────────────────


class _StorageQuotaExceeded(Exception):
    """Raised when an upload would push an account over its local-disk
    quota.  API layer translates this to HTTP 429 with a friendly
    message + current usage/quota numbers."""


class DriverDocumentsMixin(_MixinBase):
    """Per-driver document store with expiration tracking."""

    StorageQuotaExceeded = _StorageQuotaExceeded

    async def add_document(
        self,
        *,
        account_id: int,
        user_id: int,
        doc_type: str,
        bucket: str,
        object_key: str,
        file_name: str,
        file_size: Optional[int] = None,
        mime_type: Optional[str] = None,
        drive_file_id: Optional[str] = None,
        issued_at: Optional[str] = None,
        expires_at: Optional[str] = None,
        uploaded_by: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> DriverDocument:
        if doc_type not in VALID_DOC_TYPES:
            raise ValueError(f"Unknown doc_type: {doc_type}")
        now = _now_iso()
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT INTO driver_documents "
                "(account_id, user_id, doc_type, bucket, object_key, "
                " drive_file_id, file_name, file_size, mime_type, "
                " issued_at, expires_at, status, uploaded_by, "
                " uploaded_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "        'active', ?, ?, ?)",
                (account_id, user_id, doc_type, bucket, object_key,
                 drive_file_id, file_name, file_size, mime_type,
                 issued_at, expires_at, uploaded_by, now, notes),
            )
            new_id = cur.lastrowid
            # Bump per-account storage usage (only meaningful for the
            # local-disk fallback; Drive accounts cap on their side).
            if file_size:
                await self._db.execute(
                    "UPDATE accounts "
                    "SET storage_used_bytes = storage_used_bytes + ? "
                    "WHERE id = ?",
                    (file_size, account_id),
                )
        logger.info(
            "Driver doc uploaded: user=%d type=%s key=%s size=%s",
            user_id, doc_type, object_key, file_size,
        )
        return DriverDocument(
            id=new_id, account_id=account_id, user_id=user_id,
            doc_type=doc_type, bucket=bucket, object_key=object_key,
            drive_file_id=drive_file_id, file_name=file_name,
            file_size=file_size, mime_type=mime_type,
            issued_at=issued_at, expires_at=expires_at,
            status="active", uploaded_by=uploaded_by,
            uploaded_at=now, notes=notes,
        )

    async def list_documents(
        self,
        user_id: int,
        *,
        status: Optional[str] = None,
        doc_type: Optional[str] = None,
    ) -> list[DriverDocument]:
        clauses = ["user_id = ?"]
        params: list[Any] = [user_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if doc_type is not None:
            clauses.append("doc_type = ?")
            params.append(doc_type)
        where = " AND ".join(clauses)
        async with self.acquire() as conn:
            cur = await conn.execute(
                f"SELECT id, account_id, user_id, doc_type, bucket, "
                f"       object_key, drive_file_id, file_name, file_size, "
                f"       mime_type, issued_at, expires_at, status, "
                f"       uploaded_by, uploaded_at, notes "
                f"FROM driver_documents WHERE {where} "
                f"ORDER BY uploaded_at DESC",
                tuple(params),
            )
            rows = await cur.fetchall()
        return [self._row_to_document(r) for r in rows]

    async def get_document(self, doc_id: int) -> Optional[DriverDocument]:
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, doc_type, bucket, "
                "       object_key, drive_file_id, file_name, file_size, "
                "       mime_type, issued_at, expires_at, status, "
                "       uploaded_by, uploaded_at, notes "
                "FROM driver_documents WHERE id = ?",
                (doc_id,),
            )
            row = await cur.fetchone()
        return self._row_to_document(row) if row else None

    async def delete_document(self, doc_id: int) -> bool:
        """Remove the DB row + decrement the account's storage usage.
        Caller is responsible for deleting the underlying object from
        the ``ObjectStore`` (so a failure to delete the file doesn't
        orphan the DB row, or vice-versa)."""
        async with self.transaction():
            cur = await self._db.execute(
                "SELECT account_id, file_size FROM driver_documents "
                "WHERE id = ?",
                (doc_id,),
            )
            row = await cur.fetchone()
            if not row:
                return False
            acct_id, size = row
            await self._db.execute(
                "DELETE FROM driver_documents WHERE id = ?",
                (doc_id,),
            )
            if size:
                await self._db.execute(
                    "UPDATE accounts "
                    "SET storage_used_bytes = MAX(0, storage_used_bytes - ?) "
                    "WHERE id = ?",
                    (size, acct_id),
                )
        return True

    async def move_user_documents_bucket(
        self, user_id: int, old_bucket: str, new_bucket: str,
    ) -> int:
        """Rewrite the ``bucket`` column on every driver_documents row
        belonging to ``user_id`` that currently points to ``old_bucket``.

        Used by the driver-company-change archive flow:  the
        ObjectStore moves the physical folder (one Drive re-parent
        call or a ``shutil.move``), and this method updates the DB
        rows so subsequent ``get_document`` reads resolve to the new
        path.  Returns the number of rows updated.
        """
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE driver_documents "
                "SET bucket = ? "
                "WHERE user_id = ? AND bucket = ?",
                (new_bucket, user_id, old_bucket),
            )
        return cur.rowcount or 0

    async def get_expiring_documents(
        self,
        account_id: int,
        *,
        within_days: int = 30,
    ) -> list[DriverDocument]:
        """Active documents expiring within ``within_days`` from today.

        Powers the daily expiration-alert scheduler.  Compares
        ``expires_at`` (ISO date string) to today's date — past-due
        docs are also returned so the scheduler can flip them to
        ``status='expired'``.
        """
        # Cutoff is computed in Python so the SQL works on both SQLite
        # (no ``date()`` quirks) and PostgreSQL (no ``date(?, ?)`` 2-arg
        # form).  ISO date strings sort lexicographically the same as
        # the underlying dates, so we can compare ``expires_at`` directly.
        from datetime import timedelta as _td
        today = datetime.now(timezone.utc).date()
        cutoff = (today + _td(days=within_days)).isoformat()
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, account_id, user_id, doc_type, bucket, "
                "       object_key, drive_file_id, file_name, file_size, "
                "       mime_type, issued_at, expires_at, status, "
                "       uploaded_by, uploaded_at, notes "
                "FROM driver_documents "
                "WHERE account_id = ? AND status = 'active' "
                "  AND expires_at IS NOT NULL "
                "  AND expires_at <= ? "
                "ORDER BY expires_at ASC",
                (account_id, cutoff),
            )
            rows = await cur.fetchall()
        return [self._row_to_document(r) for r in rows]

    async def mark_document_status(self, doc_id: int, status: str) -> bool:
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE driver_documents SET status = ? WHERE id = ?",
                (status, doc_id),
            )
        return cur.rowcount > 0

    # ── Expiration-alert dedup ledger ──────────────────────────

    async def record_doc_notification(
        self, doc_id: int, bucket_days: int,
    ) -> bool:
        """Insert a ledger row marking that we've sent the T-``bucket_days``
        alert for this document.  Returns True if newly inserted
        (i.e. the caller should fire the alert), False if a row for
        this ``(doc_id, bucket_days)`` already exists (dedup hit).

        The composite PK enforces uniqueness at the DB level, so this
        is safe to call from concurrent schedulers.
        """
        # ``INSERT OR IGNORE`` is auto-translated to PostgreSQL's
        # ``ON CONFLICT DO NOTHING`` by the pg_adapter — that's the
        # portable way to express "insert if absent, do nothing if
        # already there".  We then read ``cur.rowcount`` to tell fresh
        # (1) from dedup (0).  Catching an exception around a raw
        # INSERT wouldn't work on PG because asyncpg aborts the whole
        # transaction on constraint violation.
        now = datetime.now(timezone.utc).isoformat()
        async with self.transaction():
            cur = await self._db.execute(
                "INSERT OR IGNORE INTO driver_document_notifications "
                "(doc_id, bucket_days, notified_at) VALUES (?, ?, ?)",
                (doc_id, bucket_days, now),
            )
        return cur.rowcount > 0

    async def get_doc_notification_buckets(self, doc_id: int) -> list[int]:
        """Return which bucket-day thresholds have already fired for
        this document.  Used by tests and admin diagnostics."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT bucket_days FROM driver_document_notifications "
                "WHERE doc_id = ? ORDER BY bucket_days DESC",
                (doc_id,),
            )
            rows = await cur.fetchall()
        return [int(r[0]) for r in rows]

    @staticmethod
    def _row_to_document(r) -> DriverDocument:
        return DriverDocument(
            id=r[0], account_id=r[1], user_id=r[2], doc_type=r[3],
            bucket=r[4], object_key=r[5], drive_file_id=r[6],
            file_name=r[7], file_size=r[8], mime_type=r[9],
            issued_at=r[10], expires_at=r[11], status=r[12],
            uploaded_by=r[13], uploaded_at=r[14], notes=r[15],
        )

    # ── Storage quota (local-disk fallback) ────────────────────

    async def get_storage_usage(self, account_id: int) -> tuple[int, int]:
        """Return ``(used_bytes, quota_bytes)`` for the account.
        ``quota_bytes`` defaults to 500 MB on fresh accounts (the
        migration default)."""
        async with self.acquire() as conn:
            cur = await conn.execute(
                "SELECT COALESCE(storage_used_bytes, 0), "
                "       COALESCE(storage_quota_bytes, 524288000) "
                "FROM accounts WHERE id = ?",
                (account_id,),
            )
            row = await cur.fetchone()
        if not row:
            return (0, 524288000)
        return (int(row[0] or 0), int(row[1] or 524288000))

    async def enforce_storage_quota(
        self, account_id: int, additional_bytes: int,
    ) -> None:
        """Raise ``StorageQuotaExceeded`` if adding ``additional_bytes``
        would push the account over its cap.

        Only meaningful when the account uses the local-disk
        fallback ObjectStore; the API layer should skip this check
        when the account is connected to Google Drive (which has
        its own quota on the user's Drive)."""
        used, quota = await self.get_storage_usage(account_id)
        if used + additional_bytes > quota:
            raise _StorageQuotaExceeded(
                f"account {account_id}: upload of {additional_bytes} bytes "
                f"would exceed quota ({used} / {quota} used)"
            )

    async def set_storage_quota(self, account_id: int, quota_bytes: int) -> bool:
        """Admin route uses this to raise/lower the per-account cap."""
        async with self.transaction():
            cur = await self._db.execute(
                "UPDATE accounts SET storage_quota_bytes = ? WHERE id = ?",
                (quota_bytes, account_id),
            )
        return cur.rowcount > 0
