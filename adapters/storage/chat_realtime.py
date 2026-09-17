"""Durable replay and leased outbox. No transport or authorization decisions."""
from uuid import uuid4


class ChatRealtimeMixin:
    async def chat_events_after(self, aid, cid, after, limit):
        cur = await self._db.execute(
            'SELECT * FROM chat_events WHERE account_id = ? AND conversation_id = ? AND event_seq > ? '
            'ORDER BY event_seq LIMIT ?', (aid, cid, after, limit))
        return [dict(row) for row in await cur.fetchall()]

    async def chat_sync_revision(self, aid, uid):
        cur = await self._db.execute('SELECT revision FROM chat_sync_state WHERE account_id = ? AND user_id = ?', (aid, uid))
        row = await cur.fetchone()
        return row['revision'] if row else 0

    async def chat_claim_deliveries(self, *, limit=200, lease_seconds=30):
        token = str(uuid4())
        cur = await self._db.execute(
            "WITH pending AS (SELECT id FROM chat_event_deliveries WHERE channel = 'realtime' "
            'AND delivered_at IS NULL AND available_at <= clock_timestamp() '
            'AND (lease_until IS NULL OR lease_until < clock_timestamp()) ORDER BY available_at, id '
            'LIMIT ? FOR UPDATE SKIP LOCKED) UPDATE chat_event_deliveries d SET '
            "lease_token = ?, lease_until = clock_timestamp() + ? * interval '1 second', attempts = attempts + 1 "
            'FROM pending WHERE d.id = pending.id RETURNING d.*', (limit, token, lease_seconds))
        return [dict(row) for row in await cur.fetchall()]

    async def chat_finish_deliveries(self, deliveries, *, success):
        if not deliveries:
            return 0
        token = deliveries[0]['lease_token']
        if any(row['lease_token'] != token for row in deliveries):
            raise ValueError('Mixed outbox leases')
        # Batch acknowledgment avoids a pool acquire/reset for every recipient.
        # All predicates still fence a crashed or replaced worker's lease.
        cur = await self._db.execute(
            'UPDATE chat_event_deliveries SET delivered_at = CASE WHEN ? THEN clock_timestamp() ELSE NULL END, '
            "available_at = clock_timestamp() + LEAST(60, power(2, LEAST(attempts, 6))) * interval '1 second', "
            'lease_until = NULL, lease_token = NULL WHERE id = ANY(?::bigint[]) '
            'AND lease_token = ? AND delivered_at IS NULL RETURNING id',
            (success, [row['id'] for row in deliveries], token))
        return len(await cur.fetchall())

    async def chat_finish_delivery(self, delivery, *, success):
        return bool(await self.chat_finish_deliveries([delivery], success=success))
