"""Driver-future tables: inspections (PTI), trainings, HOS-status cache.

The tables themselves are created by migration 050 (basic skeleton) +
migration 060 (PTI extension).  This file owns the runtime CRUD path
for each — keeping all three on one module means a future feature PR
touches a single file rather than chasing per-mixin grep-ables.

Currently implemented:
    * ``DriverInspectionsMixin`` — full PTI workflow (Phase 1: weekly).
    * ``PTITemplateMixin`` — fleet-editable checklist templates.

Stubs (filled in by their respective feature PRs):
    * ``DriverTrainingsMixin``
    * ``DriverHosStatusMixin``
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — attributes provided by the concrete DB class at runtime."""
        _db: Any
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ══════════════════════════════════════════════════════════════════
# PTI INSPECTIONS — driver-side workflow + fleet review
# ══════════════════════════════════════════════════════════════════


class DriverInspectionsMixin(_MixinBase):
    """PTI (Pre-Trip Inspection) CRUD adapter.

    Status workflow:

        scheduled  →  in_progress  →  submitted  →  approved
                                                 →  needs_service
                                                 →  rejected
                                                 →  revision_required → in_progress …

    The mixin only handles persistence; status-transition rules live
    in ``capabilities/pti/service.py`` so callers can't bypass
    business logic (e.g. submitting without all required items
    completed).
    """

    # ── create / spawn ────────────────────────────────────────────

    async def create_inspection(
        self,
        account_id: int,
        user_id: int,
        vehicle_name: str,
        *,
        inspection_type: str = "weekly",
        template_id: Optional[int] = None,
        template_version: Optional[int] = None,
        trailer_name: Optional[str] = None,
        scheduled_for: Optional[str] = None,
        due_by: Optional[str] = None,
        recurrence_parent_id: Optional[int] = None,
    ) -> int:
        """Insert a fresh inspection row.  Status defaults to
        ``'scheduled'``; the caller copies template items in a follow-up
        ``add_inspection_items`` call so the whole thing lives in a
        single transaction-equivalent block (we don't wrap in BEGIN/
        COMMIT here — sqlite + asyncpg autocommit per execute, and the
        commit at the bottom is intentional)."""
        now = _iso_now()
        cur = await self._db.execute(
            """INSERT INTO driver_inspections
               (account_id, user_id, vehicle_name, trailer_name,
                inspection_type, status, scheduled_for, due_by,
                template_id, template_version, recurrence_parent_id,
                inspected_at, created_at)
               VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id, user_id, vehicle_name, trailer_name,
                inspection_type, scheduled_for, due_by,
                template_id, template_version, recurrence_parent_id,
                now, now,
            ),
        )
        await self._db.commit()
        return cur.lastrowid

    async def add_inspection_items(
        self, inspection_id: int, items: list[dict],
    ) -> int:
        """Bulk-insert the checklist items copied from a template.

        ``items`` shape::

            [{"item_key": "brakes_service",
              "label": "Service brake response",
              "category": "brakes",
              "requires_media": False,
              "required": True,
              "sort_order": 1}, …]
        """
        if not items:
            return 0
        params = [
            (
                inspection_id,
                it["item_key"],
                it["label"],
                it.get("category", ""),
                1 if it.get("requires_media") else 0,
                1 if it.get("required", True) else 0,
                int(it.get("sort_order", 0)),
                # item_type + reference_image_url are snapshotted from
                # the template so an in-flight inspection keeps its
                # capture shape + reference photo even if the template
                # is later edited.  Default 'check' for legacy templates.
                it.get("item_type") or "check",
                it.get("reference_image_url"),
            )
            for it in items
        ]
        await self._db.executemany(
            """INSERT INTO pti_inspection_items
               (inspection_id, item_key, label, category,
                requires_media, required, sort_order,
                item_type, reference_image_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        await self._db.commit()
        return len(params)

    # ── reads ─────────────────────────────────────────────────────

    async def get_inspection(
        self, inspection_id: int, account_id: int = 0,
    ) -> Optional[dict]:
        """Fetch the inspection row (no items / media)."""
        if account_id:
            cur = await self._db.execute(
                "SELECT * FROM driver_inspections "
                "WHERE id = ? AND account_id = ?",
                (inspection_id, account_id),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM driver_inspections WHERE id = ?",
                (inspection_id,),
            )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_inspection_items(self, inspection_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_inspection_items "
            "WHERE inspection_id = ? ORDER BY sort_order, id",
            (inspection_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_inspection_media(self, inspection_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_inspection_media "
            "WHERE inspection_id = ? ORDER BY uploaded_at",
            (inspection_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_inspection_detail(
        self, inspection_id: int, account_id: int = 0,
    ) -> Optional[dict]:
        """Inspection + items + media in one shape — drives the
        dashboard detail drawer and the mini-app submit-confirmation
        screen.  Three queries, no joins (cleaner than a one-shot JOIN
        + dedupe; rowcounts are small per inspection)."""
        head = await self.get_inspection(inspection_id, account_id)
        if not head:
            return None
        head["items"] = await self.get_inspection_items(inspection_id)
        head["media"] = await self.list_inspection_media(inspection_id)
        return head

    async def list_inspections_for_user(
        self, user_id: int, *, status: Optional[str] = None, limit: int = 50,
    ) -> list[dict]:
        """Driver-side history list — used by the mini-app's PTI tab."""
        q = "SELECT * FROM driver_inspections WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        cur = await self._db.execute(q, tuple(params))
        return [dict(r) for r in await cur.fetchall()]

    async def get_open_inspection_for_user(
        self, user_id: int,
    ) -> Optional[dict]:
        """Return the driver's currently-actionable inspection, if any.

        "Open" = ``scheduled`` or ``in_progress`` or
        ``revision_required`` — the driver can take action on it.
        ``submitted``-and-awaiting-review is NOT returned here because
        the driver can't do anything until the fleet reviews; the
        mini-app surfaces it via history list instead.
        """
        cur = await self._db.execute(
            "SELECT * FROM driver_inspections "
            "WHERE user_id = ? "
            "AND status IN ('scheduled', 'in_progress', 'revision_required') "
            "ORDER BY due_by ASC, id ASC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_inspections_for_account(
        self,
        account_id: int,
        *,
        review_status: Optional[str] = None,
        status: Optional[str] = None,
        vehicle_name: Optional[str] = None,
        user_id: Optional[int] = None,
        days: int = 30,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Dashboard list — paginated, filterable.  Returns
        ``{items, total, page, page_size}``."""
        from datetime import timedelta
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(days, 1))
        ).isoformat(timespec="seconds")

        where = ["account_id = ?", "created_at >= ?"]
        params: list[Any] = [account_id, cutoff]
        if status:
            where.append("status = ?")
            params.append(status)
        if review_status:
            where.append("review_status = ?")
            params.append(review_status)
        if vehicle_name:
            where.append("LOWER(vehicle_name) = ?")
            params.append(vehicle_name.lower())
        if user_id:
            where.append("user_id = ?")
            params.append(int(user_id))
        where_sql = " AND ".join(where)

        # Count first so the caller gets the unpaged total.
        cur = await self._db.execute(
            f"SELECT COUNT(*) FROM driver_inspections WHERE {where_sql}",
            tuple(params),
        )
        total_row = await cur.fetchone()
        total = int(total_row[0]) if total_row else 0

        offset = max(0, (page - 1) * page_size)
        cur = await self._db.execute(
            f"SELECT * FROM driver_inspections WHERE {where_sql} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, int(page_size), int(offset)),
        )
        items = [dict(r) for r in await cur.fetchall()]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ── writes ────────────────────────────────────────────────────

    async def update_inspection_item(
        self,
        item_id: int,
        *,
        status: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Driver updates a single checklist item.  Returns whether a
        row was actually changed (so the route can 404 on a stale id)."""
        sets: list[str] = []
        vals: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            vals.append(status)
            if status not in ("pending", "na"):
                sets.append("completed_at = ?")
                vals.append(_iso_now())
        if notes is not None:
            sets.append("notes = ?")
            vals.append(notes)
        if not sets:
            return False
        vals.append(item_id)
        cur = await self._db.execute(
            f"UPDATE pti_inspection_items SET {', '.join(sets)} WHERE id = ?",
            tuple(vals),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def get_inspection_item(
        self, item_id: int,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_inspection_items WHERE id = ?",
            (item_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_inspection_item_by_key(
        self, inspection_id: int, item_key: str,
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_inspection_items "
            "WHERE inspection_id = ? AND item_key = ?",
            (inspection_id, item_key),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def attach_inspection_media(
        self,
        inspection_id: int,
        *,
        item_id: Optional[int] = None,
        media_type: str,
        file_path: str,
        file_name: str = "",
        file_size: int = 0,
        content_type: str = "",
        uploaded_by: int = 0,
    ) -> int:
        """Record a media row pointing at an already-uploaded
        ObjectStorage locator.  ``media_type`` is ``'photo' | 'video'``."""
        cur = await self._db.execute(
            """INSERT INTO pti_inspection_media
               (inspection_id, item_id, media_type, file_path, file_name,
                file_size, content_type, uploaded_by, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                inspection_id, item_id, media_type, file_path, file_name,
                file_size, content_type, uploaded_by, _iso_now(),
            ),
        )
        await self._db.commit()
        return cur.lastrowid

    async def get_inspection_media(self, media_id: int) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_inspection_media WHERE id = ?",
            (media_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def delete_inspection_media(self, media_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM pti_inspection_media WHERE id = ?",
            (media_id,),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def save_media_ai_review(
        self,
        media_id: int,
        *,
        status: str,
        result_json: str,
    ) -> bool:
        """Persist the per-photo AI vision verdict on a media row.

        ``status`` is ``'completed'`` or ``'error'`` (the row defaults
        to null = never checked).  ``result_json`` is the serialized
        verdict dict — stored verbatim so the dashboard + wizard can
        deserialize without a schema migration when fields are added.
        """
        cur = await self._db.execute(
            "UPDATE pti_inspection_media "
            "SET ai_review_status = ?, ai_review_result = ?, ai_reviewed_at = ? "
            "WHERE id = ?",
            (status, result_json, _iso_now(), media_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def mark_media_annotated(
        self,
        media_id: int,
        *,
        file_size: int,
        content_type: str,
    ) -> bool:
        """Stamp ``annotated_at`` + refresh size/type after the
        annotator bakes overlays into the canonical blob.

        ``file_path`` is intentionally untouched — the service layer
        keeps the same ObjectStorage path so the static mount URL doesn't
        change and any existing dashboard view doesn't 404.  Only the
        metadata that varies with the new bytes (size + MIME) is
        updated.
        """
        cur = await self._db.execute(
            "UPDATE pti_inspection_media "
            "SET annotated_at = ?, file_size = ?, content_type = ? "
            "WHERE id = ?",
            (_iso_now(), int(file_size), content_type, media_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def mark_inspection_alerted(self, inspection_id: int) -> bool:
        """Stamp ``alerted_at = now`` so the hourly reminder cron
        skips this row for the rest of the current cycle.  Cleared
        automatically when the inspection transitions out of an
        actionable status (submitted or reviewed)."""
        cur = await self._db.execute(
            "UPDATE driver_inspections SET alerted_at = ? WHERE id = ?",
            (_iso_now(), inspection_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def save_inspection_location(
        self,
        inspection_id: int,
        *,
        lat: float,
        lon: float,
        source: str,
    ) -> bool:
        """Persist WHERE the inspection happened.

        ``source`` is ``'vehicle'`` (telematics position — preferred,
        no device prompt) or ``'device'`` (browser GPS fallback).
        Called once at submit time by the service layer.
        """
        cur = await self._db.execute(
            "UPDATE driver_inspections "
            "SET location_lat = ?, location_lon = ?, "
            "    location_source = ?, location_at = ? "
            "WHERE id = ?",
            (float(lat), float(lon), source, _iso_now(), inspection_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def mark_inspection_in_progress(self, inspection_id: int) -> bool:
        """Transition ``scheduled`` → ``in_progress`` on the first
        action.  Idempotent — re-calling on an already-in-progress
        row is a no-op."""
        cur = await self._db.execute(
            "UPDATE driver_inspections SET status = 'in_progress' "
            "WHERE id = ? AND status IN ('scheduled', 'revision_required')",
            (inspection_id,),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def set_inspection_signature(
        self,
        inspection_id: int,
        *,
        role: str,
        signature_data: str,
    ) -> bool:
        """Persist a captured signature on the inspection row.

        ``role`` is ``"driver"`` (captured in the Mini App at submit
        time) or ``"reviewer"`` (captured on the dashboard when a
        compliance workflow requires a co-sign).  ``signature_data``
        is the base64 PNG produced by the canvas pad — stored verbatim
        so the rendering side can hand it back to an <img> tag without
        decode/encode round-trips.

        Idempotent: re-signing simply overwrites the prior data + bumps
        the timestamp.  Callers that want "first-sign-wins" semantics
        should check ``{role}_signed_at`` before invoking this.
        """
        if role not in ("driver", "reviewer"):
            raise ValueError(f"unknown signature role: {role!r}")
        now = _iso_now()
        # Whitelisted column names — safe to f-string into the SQL.
        col_sig  = f"{role}_signature"
        col_when = f"{role}_signed_at"
        cur = await self._db.execute(
            f"UPDATE driver_inspections "
            f"SET {col_sig} = ?, {col_when} = ? "
            f"WHERE id = ?",
            (signature_data, now, inspection_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def submit_inspection(
        self,
        inspection_id: int,
        *,
        defects_count: int,
        has_oos_defect: bool,
    ) -> bool:
        """Driver finalizes the inspection.

        The capability layer is responsible for validating that all
        required items have non-pending statuses + that
        media-required items have at least one media attached.
        """
        now = _iso_now()
        cur = await self._db.execute(
            "UPDATE driver_inspections "
            "SET status = 'submitted', submitted_at = ?, "
            "    defects_count = ?, has_oos_defect = ? "
            "WHERE id = ? AND status IN ('in_progress', 'revision_required')",
            (now, int(defects_count), 1 if has_oos_defect else 0, inspection_id),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def review_inspection(
        self,
        inspection_id: int,
        *,
        reviewer_id: int,
        review_status: str,
        review_notes: str = "",
    ) -> bool:
        """Fleet records the review outcome.

        ``review_status`` is one of:
            * ``approved``           — closed, no further action
            * ``needs_service``      — defects need a maintenance task
            * ``rejected``           — driver did the inspection wrong;
                                        a fresh one is auto-spawned
            * ``revision_required``  — driver re-opens this same record
        """
        valid = {"approved", "needs_service", "rejected", "revision_required"}
        if review_status not in valid:
            raise ValueError(f"unknown review_status: {review_status}")
        now = _iso_now()
        # When the fleet sends the inspection back for revision, the
        # row's status flips back to revision_required so the driver
        # can pick it up again.  Other review outcomes terminal-close
        # the workflow (status='reviewed').
        new_workflow_status = (
            "revision_required"
            if review_status == "revision_required"
            else "reviewed"
        )
        cur = await self._db.execute(
            "UPDATE driver_inspections "
            "SET status = ?, review_status = ?, review_notes = ?, "
            "    reviewed_at = ?, reviewed_by = ? "
            "WHERE id = ? AND status = 'submitted'",
            (
                new_workflow_status, review_status, review_notes,
                now, int(reviewer_id), inspection_id,
            ),
        )
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    # ── cron-driven queries ───────────────────────────────────────

    async def list_due_soon(
        self, account_id: int, hours_ahead: int = 24,
    ) -> list[dict]:
        """Inspections whose ``due_by`` lands within the next N hours
        and that are still actionable.  Drives the 24h-out reminder."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        cutoff = (now + timedelta(hours=hours_ahead)).isoformat(timespec="seconds")
        cur = await self._db.execute(
            "SELECT * FROM driver_inspections "
            "WHERE account_id = ? "
            "AND status IN ('scheduled', 'in_progress', 'revision_required') "
            "AND due_by IS NOT NULL AND due_by <= ?",
            (account_id, cutoff),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_overdue(self, account_id: int) -> list[dict]:
        """Inspections whose ``due_by`` is in the past and that haven't
        been submitted.  Drives the daily admin digest."""
        now = _iso_now()
        cur = await self._db.execute(
            "SELECT * FROM driver_inspections "
            "WHERE account_id = ? "
            "AND status IN ('scheduled', 'in_progress', 'revision_required') "
            "AND due_by IS NOT NULL AND due_by < ?",
            (account_id, now),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_last_submitted_inspection_for_user(
        self, user_id: int, inspection_type: str = "weekly",
    ) -> Optional[dict]:
        """Used by the spawn cron to decide whether a driver is due
        for the next weekly inspection."""
        cur = await self._db.execute(
            "SELECT * FROM driver_inspections "
            "WHERE user_id = ? AND inspection_type = ? "
            "AND submitted_at IS NOT NULL "
            # ``id DESC`` tie-break: ``submitted_at`` is ISO with second
            # precision, so two submissions inside the same wall-clock
            # second sort identically.  Falling back to ``id`` keeps the
            # newest row stable in that case.
            "ORDER BY submitted_at DESC, id DESC LIMIT 1",
            (user_id, inspection_type),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


# ══════════════════════════════════════════════════════════════════
# PTI CHECKLIST TEMPLATES — fleet-editable, versioned
# ══════════════════════════════════════════════════════════════════


class PTITemplateMixin(_MixinBase):
    """Per-account checklist template adapter.

    Templates are versioned so a fleet edit doesn't retroactively
    change inspections already in flight — when an inspection spawns
    it copies the template items into ``pti_inspection_items``
    (handled in ``DriverInspectionsMixin.add_inspection_items``).

    Only one row per ``(account_id, vehicle_type, inspection_type)``
    has ``is_active=1`` at a time; older versions stay as audit history.
    """

    # ── reads ─────────────────────────────────────────────────────

    async def get_active_template(
        self,
        account_id: int,
        vehicle_type: str,
        inspection_type: str = "weekly",
    ) -> Optional[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_checklist_templates "
            "WHERE account_id = ? AND vehicle_type = ? "
            "AND inspection_type = ? AND is_active = 1 "
            "ORDER BY version DESC LIMIT 1",
            (account_id, vehicle_type, inspection_type),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_template_items(self, template_id: int) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM pti_checklist_template_items "
            "WHERE template_id = ? ORDER BY sort_order, id",
            (template_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_active_template_with_items(
        self,
        account_id: int,
        vehicle_type: str,
        inspection_type: str = "weekly",
    ) -> Optional[dict]:
        """One-shot: active template + its items.  Used by the
        dashboard editor and the spawn cron."""
        tpl = await self.get_active_template(
            account_id, vehicle_type, inspection_type,
        )
        if not tpl:
            return None
        tpl["items"] = await self.get_template_items(int(tpl["id"]))
        return tpl

    # ── writes ────────────────────────────────────────────────────

    async def upsert_template_item(
        self,
        template_id: int,
        *,
        item_key: str,
        label: str,
        category: str = "",
        requires_media: bool = False,
        required: bool = True,
        sort_order: int = 0,
        item_type: str = "check",
        reference_image_url: Optional[str] = None,
    ) -> int:
        """Insert-or-update an item by ``(template_id, item_key)``.
        Returns the row id.

        ``reference_image_url`` is updated only when a non-None value is
        passed, so a metadata edit (label/required/etc.) doesn't wipe a
        previously-uploaded reference photo.  Pass an empty string to
        explicitly clear it.
        """
        existing = await self._db.execute(
            "SELECT id FROM pti_checklist_template_items "
            "WHERE template_id = ? AND item_key = ?",
            (template_id, item_key),
        )
        row = await existing.fetchone()
        if row:
            row_id = int(row[0])
            if reference_image_url is None:
                await self._db.execute(
                    "UPDATE pti_checklist_template_items "
                    "SET label = ?, category = ?, requires_media = ?, "
                    "    required = ?, sort_order = ?, item_type = ? "
                    "WHERE id = ?",
                    (label, category, 1 if requires_media else 0,
                     1 if required else 0, sort_order, item_type, row_id),
                )
            else:
                await self._db.execute(
                    "UPDATE pti_checklist_template_items "
                    "SET label = ?, category = ?, requires_media = ?, "
                    "    required = ?, sort_order = ?, item_type = ?, "
                    "    reference_image_url = ? "
                    "WHERE id = ?",
                    (label, category, 1 if requires_media else 0,
                     1 if required else 0, sort_order, item_type,
                     reference_image_url, row_id),
                )
        else:
            cur = await self._db.execute(
                "INSERT INTO pti_checklist_template_items "
                "(template_id, item_key, label, category, requires_media, "
                " required, sort_order, item_type, reference_image_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (template_id, item_key, label, category,
                 1 if requires_media else 0,
                 1 if required else 0,
                 sort_order, item_type, reference_image_url),
            )
            row_id = cur.lastrowid
        await self._touch_template(template_id)
        await self._db.commit()
        return row_id

    async def set_template_item_reference_image(
        self, template_id: int, item_key: str, filename: Optional[str],
    ) -> bool:
        """Attach (or clear, with None) the reference-photo filename on
        a template item.  The filename is an ObjectStorage key under the
        account's ``template_refs`` folder; the URL is built client-side
        as ``/api/inspections/template-ref/{filename}``."""
        cur = await self._db.execute(
            "UPDATE pti_checklist_template_items "
            "SET reference_image_url = ? "
            "WHERE template_id = ? AND item_key = ?",
            (filename, template_id, item_key),
        )
        if (cur.rowcount or 0) > 0:
            await self._touch_template(template_id)
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def delete_template_item(
        self, template_id: int, item_key: str,
    ) -> bool:
        cur = await self._db.execute(
            "DELETE FROM pti_checklist_template_items "
            "WHERE template_id = ? AND item_key = ?",
            (template_id, item_key),
        )
        if (cur.rowcount or 0) > 0:
            await self._touch_template(template_id)
        await self._db.commit()
        return (cur.rowcount or 0) > 0

    async def reorder_template_items(
        self, template_id: int, ordered_keys: list[str],
    ) -> int:
        """Bulk reorder via a list of ``item_key`` in display order."""
        if not ordered_keys:
            return 0
        params = [
            (i, template_id, k) for i, k in enumerate(ordered_keys, start=1)
        ]
        await self._db.executemany(
            "UPDATE pti_checklist_template_items "
            "SET sort_order = ? "
            "WHERE template_id = ? AND item_key = ?",
            params,
        )
        await self._touch_template(template_id)
        await self._db.commit()
        return len(params)

    async def reset_template_to_default(
        self,
        account_id: int,
        vehicle_type: str,
        inspection_type: str = "weekly",
        default_items: Optional[list[dict]] = None,
    ) -> int:
        """Replace the active template with a fresh copy of the
        defaults.  Bumps the version so historic inspections still
        reference the old template.

        ``default_items`` is the list of dicts from
        ``capabilities/pti/templates.py``.  Required when resetting
        from the dashboard endpoint; not used internally.
        """
        if default_items is None:
            raise ValueError("default_items required for reset")
        now = _iso_now()
        # Deactivate the existing template + bump version
        old = await self.get_active_template(
            account_id, vehicle_type, inspection_type,
        )
        new_version = (int(old["version"]) + 1) if old else 1
        if old:
            await self._db.execute(
                "UPDATE pti_checklist_templates SET is_active = 0, "
                "updated_at = ? WHERE id = ?",
                (now, int(old["id"])),
            )
        cur = await self._db.execute(
            "INSERT INTO pti_checklist_templates "
            "(account_id, vehicle_type, inspection_type, version, "
            " is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (account_id, vehicle_type, inspection_type, new_version, now, now),
        )
        new_id = cur.lastrowid
        for it in default_items:
            await self._db.execute(
                "INSERT INTO pti_checklist_template_items "
                "(template_id, item_key, label, category, requires_media, "
                " required, sort_order, item_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id,
                    it["item_key"], it["label"], it.get("category", ""),
                    1 if it.get("requires_media") else 0,
                    1 if it.get("required", True) else 0,
                    int(it.get("sort_order", 0)),
                    it.get("item_type") or "check",
                ),
            )
        await self._db.commit()
        return int(new_id)

    async def seed_account_pti_templates(
        self,
        account_id: int,
        truck_items: list[dict],
        trailer_items: list[dict],
    ) -> None:
        """Create the default truck + trailer templates for a brand-new
        account.  Idempotent — no-op when the templates already exist.
        Called from ``create_account`` so new tenants get the seed
        without depending on the one-time migration backfill."""
        for vt, items in (("truck", truck_items), ("trailer", trailer_items)):
            existing = await self.get_active_template(account_id, vt, "weekly")
            if existing:
                continue
            now = _iso_now()
            cur = await self._db.execute(
                "INSERT INTO pti_checklist_templates "
                "(account_id, vehicle_type, inspection_type, version, "
                " is_active, created_at, updated_at) "
                "VALUES (?, ?, 'weekly', 1, 1, ?, ?)",
                (account_id, vt, now, now),
            )
            template_id = cur.lastrowid
            for it in items:
                await self._db.execute(
                    "INSERT INTO pti_checklist_template_items "
                    "(template_id, item_key, label, category, "
                    " requires_media, required, sort_order, item_type) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        template_id,
                        it["item_key"], it["label"], it.get("category", ""),
                        1 if it.get("requires_media") else 0,
                        1 if it.get("required", True) else 0,
                        int(it.get("sort_order", 0)),
                        it.get("item_type") or "check",
                    ),
                )
        await self._db.commit()

    async def _touch_template(self, template_id: int) -> None:
        """Bump ``updated_at`` whenever an item changes — drives the
        dashboard "last edited" hint."""
        await self._db.execute(
            "UPDATE pti_checklist_templates SET updated_at = ? WHERE id = ?",
            (_iso_now(), template_id),
        )


# ══════════════════════════════════════════════════════════════════
# Remaining stubs (filled in by their respective feature PRs)
# ══════════════════════════════════════════════════════════════════


class DriverTrainingsMixin:
    """Driver training records adapter — empty stub.

    Schema available: ``driver_trainings``.
    """


class DriverHosStatusMixin:
    """HOS / duty-status cache adapter.

    Reads from ``driver_hos_status`` (one row per driver, refreshed by
    the HOS sync job from Samsara).  Used by the AI agent's
    ``get_driver_hos_status`` tool to answer "how many hours does X
    have left", "who's out of hours", etc.  All writes still flow
    through the sync job, not these read methods.
    """

    async def get_driver_hos_status(
        self,
        account_id: int,
        user_id: int | None = None,
    ) -> list[dict]:
        """Return cached HOS rows for an account.

        With ``user_id`` set, returns just that driver's row (still as
        a list so the caller's shape is consistent).  Joins ``users``
        on user_id so the caller gets ``display_name`` / ``truck_num``
        in one query rather than two round-trips per driver.
        """
        if user_id is not None:
            sql = (
                "SELECT h.user_id, h.samsara_driver_id, h.duty_status, "
                "       h.drive_seconds_today, h.on_duty_seconds_today, "
                "       h.cycle_seconds_remaining, h.shift_seconds_remaining, "
                "       h.last_status_change, h.updated_at, "
                "       u.display_name, u.truck_num "
                "FROM driver_hos_status h "
                "LEFT JOIN users u ON u.id = h.user_id "
                "WHERE h.account_id = ? AND h.user_id = ?"
            )
            params: tuple = (account_id, user_id)
        else:
            sql = (
                "SELECT h.user_id, h.samsara_driver_id, h.duty_status, "
                "       h.drive_seconds_today, h.on_duty_seconds_today, "
                "       h.cycle_seconds_remaining, h.shift_seconds_remaining, "
                "       h.last_status_change, h.updated_at, "
                "       u.display_name, u.truck_num "
                "FROM driver_hos_status h "
                "LEFT JOIN users u ON u.id = h.user_id "
                "WHERE h.account_id = ? "
                "ORDER BY u.display_name"
            )
            params = (account_id,)
        cur = await self._db.execute(sql, params)
        rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({
                "user_id": r[0],
                "samsara_driver_id": r[1] or "",
                "duty_status": r[2] or "unknown",
                "drive_seconds_today": r[3] or 0,
                "on_duty_seconds_today": r[4] or 0,
                "cycle_seconds_remaining": r[5],
                "shift_seconds_remaining": r[6],
                "last_status_change": r[7] or "",
                "updated_at": r[8] or "",
                "display_name": r[9] or "",
                "truck_num": r[10] or "",
            })
        return out
