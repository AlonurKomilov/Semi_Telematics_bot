"""Per-worker coalesced wakeups. Redis carries identity references, never content.

A dropped hint is harmless: sockets reconcile their durable scan cursors on a
bounded timer. One Redis subscription per worker, not per browser. No globals.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
import json
import logging

logger = logging.getLogger(__name__)
CHANNEL = 'chat:wake:v1'


class RedisSignals:
    def __init__(self, client_provider):
        self.client_provider = client_provider

    def client(self):
        client = self.client_provider()
        if client is None:
            raise ConnectionError('Chat pubsub unavailable')
        return client

    async def publish_many(self, recipients):
        async with self.client().pipeline(transaction=False) as pipeline:
            for account_id, user_id in recipients:
                pipeline.publish(CHANNEL, json.dumps([account_id, user_id]))
            await asyncio.wait_for(pipeline.execute(), 2)

    async def listen(self, wake):
        async with self.client().pubsub() as subscription:
            await asyncio.wait_for(subscription.subscribe(CHANNEL), 2)
            while True:
                message = await subscription.get_message(ignore_subscribe_messages=True, timeout=1)
                if message:
                    try:
                        value = json.loads(message['data'])
                        if (isinstance(value, list) and len(value) == 2
                                and all(type(v) is int and v > 0 for v in value)):
                            wake(*value)
                    except (TypeError, ValueError):
                        # A hint that does not parse wakes nobody; the
                        # periodic replay reads the truth from the table.
                        pass


class ChatRuntime:
    def __init__(self, db, signals, *, poll_seconds=2, max_connections=1000, per_user=5):
        self.db, self.signals = db, signals
        self.poll_seconds = poll_seconds
        self.max_connections, self.per_user = max_connections, per_user
        self.connections = {}
        self.tasks = []
        self.stats = {'published': 0, 'retries': 0, 'subscriber_reconnects': 0}

    @asynccontextmanager
    async def register(self, actor):
        key = (actor.account_id, actor.user_id)
        if (sum(map(len, self.connections.values())) >= self.max_connections
                or len(self.connections.get(key, ())) >= self.per_user):
            raise OverflowError('Chat connection limit')
        wake = asyncio.Event()
        self.connections.setdefault(key, set()).add(wake)
        try:
            yield wake
        finally:
            self.connections[key].discard(wake)
            if not self.connections[key]:
                del self.connections[key]

    def wake(self, aid, uid):
        for signal in self.connections.get((aid, uid), ()):
            signal.set()  # coalesces unbounded publication bursts into one bit

    async def drain_once(self):
        rows = await self.db.chat_claim_deliveries()
        # One pipeline and one DB acknowledgment per leased batch. Recipient
        # hints coalesce, while each durable outbox row keeps its lease fence.
        recipients = {(row['account_id'], row['recipient_user_id']) for row in rows}
        for key in recipients:
            self.wake(*key)
        if not rows:
            return 0
        try:
            await self.signals.publish_many(recipients)
            success = True
        except Exception:
            success = False  # partially published pipelines may safely repeat
        await self.db.chat_finish_deliveries(rows, success=success)
        self.stats['published' if success else 'retries'] += len(rows)
        return len(rows)

    async def _drain(self):
        while True:
            try:
                count = await self.drain_once()
            except Exception:
                logger.warning('Chat outbox unavailable; durable retry pending')
                count = 0
            await asyncio.sleep(.05 if count else .25)

    async def _listen(self):
        while True:
            try:
                await self.signals.listen(self.wake)
            except Exception:
                self.stats['subscriber_reconnects'] += 1
            await asyncio.sleep(1)

    async def start(self):
        self.tasks = [asyncio.create_task(self._drain(), name='chat-outbox'),
                      asyncio.create_task(self._listen(), name='chat-pubsub')]
        return self

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()
