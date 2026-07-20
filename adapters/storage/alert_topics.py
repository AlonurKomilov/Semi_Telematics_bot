"""User-defined custom alert topics — CRUD mixin.

A custom topic is a NAMED routing rule inside one role's group: one
alert type, optionally narrowed to sub-categories, optionally posted
into its own Telegram forum thread.  The resolver treats a matching
custom topic as REPLACING the role's default flat post for that alert;
deleting a topic removes only the rule — the Telegram thread and its
message history are never touched.
"""

from __future__ import annotations

from typing import Optional

from .models import AlertTopic


class AlertTopicsMixin:
    # ``self._db`` / ``self._now`` come from the concrete Database —
    # NEVER stub them here (a stub would shadow Database._now via the
    # MRO and null every created_at; see bot_instances.py history).

    async def create_alert_topic(
        self,
        *,
        account_id: int,
        persona: str,
        name: str,
        alert_type: str,
        subtypes: str = "",
        thread_id: Optional[int] = None,
    ) -> AlertTopic:
        now = self._now()
        cur = await self._db.execute(
            """INSERT INTO alert_topics
               (account_id, persona, name, alert_type, subtypes,
                thread_id, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (account_id, persona, name, alert_type, subtypes,
             thread_id, now, now),
        )
        await self._db.commit()
        row = await self.get_alert_topic(account_id, cur.lastrowid)
        assert row is not None
        return row

    async def get_alert_topic(
        self, account_id: int, topic_id: int
    ) -> Optional[AlertTopic]:
        cur = await self._db.execute(
            "SELECT * FROM alert_topics WHERE account_id = ? AND id = ? LIMIT 1",
            (account_id, topic_id),
        )
        row = await cur.fetchone()
        return self._row_to_alert_topic(row) if row else None

    async def list_alert_topics(
        self, account_id: int, persona: Optional[str] = None
    ) -> list[AlertTopic]:
        if persona is None:
            cur = await self._db.execute(
                "SELECT * FROM alert_topics WHERE account_id = ? AND is_active = 1 "
                "ORDER BY persona ASC, name ASC",
                (account_id,),
            )
        else:
            cur = await self._db.execute(
                "SELECT * FROM alert_topics WHERE account_id = ? AND persona = ? "
                "AND is_active = 1 ORDER BY name ASC",
                (account_id, persona),
            )
        rows = await cur.fetchall()
        return [self._row_to_alert_topic(r) for r in rows]

    async def delete_alert_topic(self, account_id: int, topic_id: int) -> bool:
        cur = await self._db.execute(
            "DELETE FROM alert_topics WHERE account_id = ? AND id = ?",
            (account_id, topic_id),
        )
        await self._db.commit()
        return bool(getattr(cur, "rowcount", 0))

    def _row_to_alert_topic(self, row) -> AlertTopic:
        d = dict(row)
        return AlertTopic(
            id=d["id"],
            account_id=d["account_id"],
            persona=d["persona"],
            name=d["name"],
            alert_type=d["alert_type"],
            subtypes=d["subtypes"] or "",
            thread_id=d["thread_id"],
            is_active=bool(d["is_active"]),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )
