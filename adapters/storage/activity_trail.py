"""The unified activity trail — one store for "who did what".

``activity_events`` is the universal per-field accountability table
(owner decision 2026-07-31, advisor-ruled architecture): every feature
that adopts the trail writes here; the two pre-existing rich trails
(``load_events``, ``vehicle_inventory_events``) stay in place and are
unioned at READ time by the facade.  RULE: no fourth per-feature event
table, ever — new features adopt THIS one.

The contract (enforced by tests in tests/test_activity_trail.py):

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
    # ruling: exactly three arms, never a fourth); the frozen thin
    # audit_log remains readable until its human rows migrate in
    # (Phase 4).  These account-wide reads exist ONLY for the facade —
    # per-record lenses keep using each feature's own list method.

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

    # Machine churn the frozen audit_log accumulated (alert lifecycle
    # etc.) — excluded from the people-only reader.
    MACHINE_AUDIT_ACTIONS: tuple[str, ...] = (
        "alert_realerted", "alert_max_realerts", "alert_auto_resolved",
        "alert_expired", "alerts_ttl_close", "alert_escalated",
        "alert_max_escalation",
    )

    async def list_trail_legacy_audit(
        self, account_id: int, *, limit: int = 100,
        before_ts: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("?" * len(self.MACHINE_AUDIT_ACTIONS))
        extra = " AND created_at < ?" if before_ts else ""
        params: list[Any] = [account_id, *self.MACHINE_AUDIT_ACTIONS]
        if before_ts:
            params.append(before_ts)
        params.append(limit)
        cur = await self._db.execute(
            f"""SELECT id, user_id, action, target_type, target_id,
                       details, created_at
                FROM audit_log
                WHERE account_id = ? AND action NOT IN ({placeholders}){extra}
                ORDER BY created_at DESC LIMIT ?""",
            tuple(params),
        )
        return [
            {"id": r[0], "user_id": r[1], "action": r[2],
             "target_type": r[3], "target_id": r[4],
             "details": r[5], "created_at": r[6]}
            for r in await cur.fetchall()
        ]

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
