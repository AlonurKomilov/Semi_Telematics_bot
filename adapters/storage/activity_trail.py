"""The unified activity trail — one store for "who did what".

``activity_events`` is the universal per-field accountability table
(owner decision 2026-07-31, advisor-ruled architecture): every feature
that adopts the trail writes here; the two pre-existing rich trails
(``load_events``, ``vehicle_inventory_events``) stay in place and are
unioned at READ time by the facade.  RULE: no fourth per-feature event
table, ever — new features adopt THIS one.

The contract (enforced by tests in capabilities/activity_trail/tests/test_activity_trail.py):

* **People only.**  Events record HUMAN actions.  ``actor_user_id`` is
  the platform ``users.id`` (via ``resolve_user_id`` — never
  ``user["id"]``, never a telegram sub).  The rare system-on-behalf
  write passes ``actor_user_id=None`` AND names itself in
  ``context={"system": "<why>"}``.  Machine churn (alert lifecycle,
  telemetry sync) never lands here — that noise is what made the old
  thin audit_log unreadable.
* **Same transaction.**  ``append_activity_events`` never commits: the
  caller records the event inside the SAME transaction as the mutation
  it describes, so a trail row can neither survive a rolled-back write
  nor go missing after a committed one.
* **Deletes carry the body.**  A delete event's ``changes`` holds every
  field as ``{"from": value, "to": None}`` — the trail IS the recovery
  record.  (Lesson of the 2026-07-30 maintenance bulk delete, where
  values weren't captured and 15 units' targets became unrecoverable.)
* **Bulk = N events, one group.**  A bulk action writes one event per
  entity, all sharing a ``group_id`` — never an id list in one row
  (the ``task_ids[:10]`` truncation bug).  Readers collapse by group.

Diff/changes helpers live in ``capabilities/activity_trail`` — this
module is only the SQL.
"""

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)


class RestoreConflict(Exception):
    """The restore target id is occupied (or the row vanished) — the
    caller maps this to a 409, never a silent overwrite."""


if TYPE_CHECKING:
    class _MixinBase:
        """Typing stub — provided by the concrete Database at runtime."""
        _db: Any
        def transaction(self) -> Any: ...
        @staticmethod
        def _now() -> str: ...
else:
    _MixinBase = object


class ActivityTrailMixin(_MixinBase):

    async def append_activity_events(
        self,
        account_id: int,
        events: list[dict[str, Any]],
    ) -> None:
        """INSERT trail rows.  NO commit — rides the caller's transaction.

        Each event dict: ``entity_type`` (str), ``entity_id`` (str|int),
        ``action`` (str) required; ``changes`` (dict), ``actor_user_id``
        (int|None), ``group_id`` (str), ``context`` (dict), ``note``
        (str) optional.
        """
        now = self._now()
        for e in events:
            context = dict(e.get("context") or {})
            if e.get("actor_user_id") is None and "system" not in context:
                raise ValueError(
                    "activity event without actor_user_id must declare "
                    "context={'system': '<why>'} — the trail records people"
                )
            await self._db.execute(
                """INSERT INTO activity_events
                   (account_id, entity_type, entity_id, action, changes,
                    actor_user_id, group_id, context, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (account_id, str(e["entity_type"]), str(e["entity_id"]),
                 str(e["action"]), json.dumps(e.get("changes") or {}),
                 e.get("actor_user_id"), e.get("group_id"),
                 json.dumps(context), (e.get("note") or "")[:500], now),
            )

    async def list_activity_events(
        self,
        account_id: int,
        *,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        actor_user_id: Optional[int] = None,
        action: Optional[str] = None,
        group_id: Optional[str] = None,
        before_id: Optional[int] = None,
        before_ts: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Trail rows, newest first — serves BOTH read lenses: the
        per-record History card (entity_type + entity_id) and the
        account-wide audit page (time/actor scoped).  Ids only — the
        API layer resolves display names."""
        where = ["account_id = ?"]
        params: list[Any] = [account_id]
        if entity_type is not None:
            where.append("entity_type = ?"); params.append(entity_type)
        if entity_id is not None:
            where.append("entity_id = ?"); params.append(str(entity_id))
        if actor_user_id is not None:
            where.append("actor_user_id = ?"); params.append(actor_user_id)
        if action is not None:
            where.append("action = ?"); params.append(action)
        if group_id is not None:
            where.append("group_id = ?"); params.append(group_id)
        if before_id is not None:
            where.append("id < ?"); params.append(before_id)
        if before_ts is not None:
            where.append("created_at < ?"); params.append(before_ts)
        params.append(max(1, min(int(limit), 500)))
        cur = await self._db.execute(
            f"""SELECT id, entity_type, entity_id, action, changes,
                       actor_user_id, group_id, context, note, created_at
                FROM activity_events
                WHERE {' AND '.join(where)}
                ORDER BY id DESC LIMIT ?""",
            tuple(params),
        )
        rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": r[0], "entity_type": r[1], "entity_id": r[2],
                "action": r[3], "changes": _loads(r[4]),
                "actor_user_id": r[5], "group_id": r[6],
                "context": _loads(r[7]), "note": r[8], "created_at": r[9],
            })
        return out

    # ── Legacy read arms for the unified facade ───────────────────
    # The two shipped rich trails stay in their own tables (advisor
    # ruling: exactly three arms, never a fourth).  These account-wide
    # reads exist ONLY for the facade — per-record lenses keep using
    # each feature's own list method.  (audit_log's human rows were
    # imported by migration 178; it is machine-only now and unread.)

    async def list_trail_legacy_loads(
        self, account_id: int, *, limit: int = 100,
        before_ts: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        extra = " AND created_at < ?" if before_ts else ""
        params: tuple = (account_id, before_ts, limit) if before_ts else (account_id, limit)
        cur = await self._db.execute(
            f"""SELECT id, load_id, event_type, changes, actor_user_id,
                       dispatcher_user_id, note, created_at
                FROM load_events WHERE account_id = ?{extra}
                ORDER BY created_at DESC LIMIT ?""",
            params,
        )
        return [
            {"id": r[0], "load_id": r[1], "event_type": r[2],
             "changes": _loads(r[3]), "actor_user_id": r[4],
             "dispatcher_user_id": r[5], "note": r[6], "created_at": r[7]}
            for r in await cur.fetchall()
        ]

    async def list_trail_legacy_inventory(
        self, account_id: int, *, limit: int = 100,
        before_ts: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        extra = " AND created_at < ?" if before_ts else ""
        params: tuple = (account_id, before_ts, limit) if before_ts else (account_id, limit)
        cur = await self._db.execute(
            f"""SELECT id, item_id, event_type, from_status, to_status,
                       from_vehicle_id, to_vehicle_id, actor_user_id,
                       driver_user_id, note, created_at
                FROM vehicle_inventory_events WHERE account_id = ?{extra}
                ORDER BY created_at DESC LIMIT ?""",
            params,
        )
        return [
            {"id": r[0], "item_id": r[1], "event_type": r[2],
             "from_status": r[3], "to_status": r[4],
             "from_vehicle_id": r[5], "to_vehicle_id": r[6],
             "actor_user_id": r[7], "driver_user_id": r[8],
             "note": r[9], "created_at": r[10]}
            for r in await cur.fetchall()
        ]

    async def get_activity_event(
        self, account_id: int, event_id: int,
    ) -> Optional[dict[str, Any]]:
        """One trail row by id, account-scoped (the restore endpoint's
        lookup — never trust a client-supplied id across tenants)."""
        cur = await self._db.execute(
            """SELECT id, entity_type, entity_id, action, changes,
                      actor_user_id, group_id, context, note, created_at
               FROM activity_events WHERE id = ? AND account_id = ?""",
            (event_id, account_id),
        )
        r = await cur.fetchone()
        if not r:
            return None
        return {
            "id": r[0], "entity_type": r[1], "entity_id": r[2],
            "action": r[3], "changes": _loads(r[4]),
            "actor_user_id": r[5], "group_id": r[6],
            "context": _loads(r[7]), "note": r[8], "created_at": r[9],
        }

    # ── Restore-from-trail: the write side ───────────────────────
    # A delete event's changes ARE the row body ({field: {from, to:null}}).
    # Restoring replays the from-values into an INSERT.  The column
    # ALLOWLIST per table is the safety boundary: the trail's stored
    # field names are data, and data never chooses SQL identifiers.
    # Restores keep the ORIGINAL id when free so the record's history
    # chain stays on one id (created → deleted → restored); an occupied
    # id raises RestoreConflict rather than guessing.

    RESTORABLE_COLUMNS: dict[str, frozenset[str]] = {
        "maintenance_tasks": frozenset({
            "company_code", "vehicle_id", "vehicle_name", "task_type",
            "service_task_id", "description", "due_date", "due_miles",
            "due_engine_hours", "priority", "status", "created_by",
            "created_at", "recur_interval_days", "recur_interval_miles",
            "recur_interval_engine_hours", "work_order_id",
            "spawned_from_id", "last_odometer", "last_engine_hours",
            "completed_at", "cost_cents", "vendor_name",
            "attachment_path", "attachment_name", "attachment_content_type",
            "attested_by", "attested_at",
            # alerted_at / warning_sent_at / snoozed_until deliberately
            # NOT restorable — a resurrected task re-arms its alerts.
        }),
        "work_orders": frozenset({
            "company_code", "vehicle_id", "vehicle_name", "vendor_name",
            "vendor_address", "vendor_phone", "service_date",
            "odometer_at_service", "engine_hours_at_service", "labor_cost",
            "parts_cost", "tax_amount", "total_cost", "invoice_number",
            "payment_method", "payment_status", "status", "notes",
            "created_by", "created_at", "source", "external_id",
            "vehicle_type", "assignee", "assigned_to", "repair_priority",
            "complaint", "cause", "correction", "vendor_id",
            "external_number", "fee_amount", "registry_id",
        }),
        "service_tasks": frozenset({
            "name", "name_key", "canonical_key", "description",
            "expected_labor_hours", "parent_id", "status", "created_by",
            "created_at", "vehicle_type", "system_key", "assembly_key",
        }),
        "maintenance_templates": frozenset({
            "name", "task_type", "description", "priority", "due_in_days",
            "due_in_miles", "due_in_hours", "recur_interval_days",
            "recur_interval_miles", "recur_interval_engine_hours",
            "created_by", "created_at",
        }),
        "work_hours": frozenset({
            "label", "start_hour", "end_hour", "target_role",
            "created_by", "created_at",
        }),
        "vendors": frozenset({
            "name", "name_key", "address", "phone", "email", "notes",
            "created_at", "global_vendor_id",
        }),
    }

    async def get_row_company_code(
        self, account_id: int, table: str, entity_id: int,
    ) -> Optional[str]:
        """The LIVE row's company_code, or None when the row is gone.

        The company wall on history can't be read off the events: only
        delete bodies carry ``company_code`` (create/update diffs record
        what changed, and the company rarely does).  So the owning row
        is the source of truth, with the delete body as the fallback for
        records that no longer exist.  Same allowlist discipline as the
        restore writer — the table name is never caller-supplied.
        """
        allowed = self.RESTORABLE_COLUMNS.get(table)
        if allowed is None or "company_code" not in allowed:
            raise ValueError(f"table {table!r} carries no company scope")
        cur = await self._db.execute(
            f"SELECT company_code FROM {table} WHERE id = ? AND account_id = ?",
            (entity_id, account_id),
        )
        r = await cur.fetchone()
        return (r[0] or "") if r else None

    async def get_rows_company_codes(
        self, account_id: int, table: str, ids: list[int],
    ) -> dict[int, str]:
        """Batched ``get_row_company_code`` — one query per TABLE, not per row.

        The aggregate feed needs the company of every company-scoped row
        on the page.  Row-at-a-time would be N+1; there are only ever
        two owning tables (maintenance_tasks, work_orders), so the whole
        wall costs at most two queries per page, and none at all for an
        unrestricted viewer.

        Missing ids are simply absent from the result — the caller
        decides what an unresolvable row means (for a restricted viewer:
        withheld).  Same allowlist discipline as the single-row reader
        and the restore writer: the table name is never caller-supplied
        from the wire.
        """
        allowed = self.RESTORABLE_COLUMNS.get(table)
        if allowed is None or "company_code" not in allowed:
            raise ValueError(f"table {table!r} carries no company scope")
        if not ids:
            return {}
        out: dict[int, str] = {}
        # Chunked so a huge page cannot build an unbounded IN list.
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" for _ in chunk)
            cur = await self._db.execute(
                f"SELECT id, company_code FROM {table} "
                f"WHERE account_id = ? AND id IN ({marks})",
                (account_id, *chunk),
            )
            for r in await cur.fetchall():
                out[int(r[0])] = r[1] or ""
        return out

    async def restore_trail_row(
        self, account_id: int, table: str, entity_id: int,
        row: dict[str, Any],
    ) -> int:
        """INSERT a restored row with its original id.  NO commit —
        the engine wraps this and the 'restore' event in one
        transaction.  Raises RestoreConflict if the id is occupied."""
        allowed = self.RESTORABLE_COLUMNS.get(table)
        if allowed is None:
            raise ValueError(f"table {table!r} is not restorable")
        cur = await self._db.execute(
            f"SELECT 1 FROM {table} WHERE id = ? AND account_id = ?",
            (entity_id, account_id),
        )
        if await cur.fetchone():
            raise RestoreConflict(
                f"{table} id {entity_id} already exists — nothing restored",
            )
        cols = {k: v for k, v in row.items() if k in allowed and v is not None}
        if not cols:
            raise ValueError("restore body carried no restorable fields")
        # Events recorded before created_at joined the delete body (and
        # NOT NULL columns generally): default the stamp so old events
        # stay restorable — the trail must honor its past selves.
        if "created_at" in allowed and "created_at" not in cols:
            cols["created_at"] = self._now()
        names = ["id", "account_id", *cols.keys()]
        marks = ",".join("?" * len(names))
        await self._db.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({marks})",
            (entity_id, account_id, *cols.values()),
        )
        return entity_id

    async def reactivate_trail_row(
        self, account_id: int, table: str, entity_id: int,
    ) -> int:
        """Soft-deleted entities (vehicles) restore by flipping back to
        active.  NO commit — rides the engine's transaction."""
        if table != "vehicles":
            raise ValueError(f"table {table!r} has no reactivate path")
        cur = await self._db.execute(
            "UPDATE vehicles SET is_active = 1, status = 'active', "
            "updated_at = ? WHERE id = ? AND account_id = ?",
            (self._now(), entity_id, account_id),
        )
        if not cur.rowcount:
            raise RestoreConflict(f"vehicle {entity_id} not found")
        return entity_id

    async def count_actor_actions(
        self, account_id: int, actor_user_id: int,
        entity_type: str, action: str, *, days: int = 14,
    ) -> dict:
        """One user's OWN recent action counts — the spotlight signals.

        Returns ``{"total": n, "solo": s, "grouped": g}`` where solo is
        events written with no group_id (one-at-a-time work) and
        grouped is events that rode a bulk group — the "already uses
        the bulk path" signal.  Self-scope is the caller's contract:
        this is only ever called with the REQUESTING user's id
        (capabilities/spotlight/router.py), never to profile others.
        """
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = await self._db.execute(
            "SELECT COUNT(*), "
            "       COUNT(*) FILTER (WHERE group_id IS NULL OR group_id = ''), "
            "       COUNT(*) FILTER (WHERE group_id IS NOT NULL AND group_id <> '') "
            "FROM activity_events "
            "WHERE account_id = ? AND actor_user_id = ? "
            "  AND entity_type = ? AND action = ? AND created_at >= ?",
            (account_id, actor_user_id, entity_type, action, since),
        )
        row = await cur.fetchone()
        return {"total": row[0], "solo": row[1], "grouped": row[2]}

    async def prune_activity_events(
        self, account_id: int, days_keep: int,
    ) -> int:
        """Retention hook for the data_lifecycle hub (accountability
        data — the window should be YEARS, not months)."""
        from datetime import datetime, timedelta, timezone
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days_keep)
        ).isoformat()
        cur = await self._db.execute(
            "DELETE FROM activity_events WHERE account_id = ? AND created_at < ?",
            (account_id, cutoff),
        )
        await self._db.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def _loads(raw: Any) -> dict:
    try:
        return json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
