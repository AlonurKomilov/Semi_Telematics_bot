"""Real Postgres replay/fanout/lease invariants, including hidden scan gaps."""
import asyncio
import pytest
from capabilities.chat.models import ChatError
from capabilities.chat.realtime import ChatRuntime
from capabilities.chat.tests.test_messages import rows
from uuid import uuid4


async def room(team, **kwargs):
    return await team.service.create_group(team.actor('owner'), title='Realtime', **kwargs)


async def send(team, cid, *, key=None, body='Message'):
    return (await team.service.send_message(team.actor('owner'), cid, body=body, client_message_id=key or str(uuid4())))['message']
from capabilities.chat.tests.test_access import change_user


async def test_replay_uses_current_tombstone_and_checkpoint(team):
    c = await room(team)
    checkpoint = await team.service.replay(team.actor('peer'), c['id'])
    assert checkpoint['resync_required'] and checkpoint['cursor'] == 1
    msg = await send(team, c['id'], body='old secret')
    await team.service.edit_message(team.actor('owner'), c['id'], msg['id'], expected_version=1, body='new secret')
    await team.service.delete_message(team.actor('owner'), c['id'], msg['id'], expected_version=2)
    replay = await team.service.replay(team.actor('peer'), c['id'], after=1)
    assert len(replay['items']) == 3 and replay['cursor'] == 4
    assert all(e['message']['body'] is None and e['message']['version'] == 3 for e in replay['items'])
    assert 'secret' not in str(replay)
    assert not replay['resync_required']


async def test_hidden_and_private_events_advance_scan_without_disclosure(team):
    c = await room(team, kind='roles', role_keys=['owner', 'dispatcher'], new_member_history='since_join')
    old = await send(team, c['id'], body='hidden')
    await change_user(team, 'peer', role='hr')
    await send(team, c['id'], key='absent', body='also hidden')
    await change_user(team, 'peer', role='dispatcher')
    await team.service.draft(team.actor('owner'), c['id'], expected_version=0, body='private draft')
    await team.service.edit_message(team.actor('owner'), c['id'], old['id'], expected_version=1, body='still hidden')
    visible = await send(team, c['id'], key='visible', body='visible')
    first = await team.service.replay(team.actor('peer'), c['id'], after=1, limit=3)
    assert first['items'] == [] and first['cursor'] == 4 and first['has_more']
    second = await team.service.replay(team.actor('peer'), c['id'], after=4)
    assert len(second['items']) == 1 and second['items'][0]['message']['id'] == visible['id']
    assert second['scope']['visible_after_message_seq'] == 2
    assert not second['resync_required']


async def test_trimmed_or_future_cursor_requires_snapshot(team):
    c = await room(team)
    await send(team, c['id'])
    await team.db._db.execute('DELETE FROM chat_events WHERE conversation_id = ? AND event_seq = 1', (c['id'],))
    for cursor in (0, 999):
        result = await team.service.replay(team.actor('peer'), c['id'], after=cursor)
        assert result['resync_required'] and result['cursor'] == 2 and result['items'] == []
    assert not (await team.service.replay(team.actor('peer'), c['id'], after=1))['resync_required']


async def test_metadata_and_personal_fanout_and_membership_revision(team):
    c = await room(team, kind='roles', role_keys=['owner', 'dispatcher'])
    revision = await team.service.sync_revision(team.actor('peer'))
    await change_user(team, 'peer', role='hr')
    assert await team.service.sync_revision(team.actor('peer')) > revision
    with pytest.raises(ChatError, match='not_found'):
        await team.service.replay(team.actor('peer'), c['id'], after=0)
    await team.service.draft(team.actor('owner'), c['id'], expected_version=0, body='private')
    event = (await rows(team, 'chat_events'))[-1]
    assert event['recipient_user_id'] == team.users['owner'].id
    deliveries = [d for d in await rows(team, 'chat_event_deliveries') if d['event_id'] == event['id']]
    assert [d['recipient_user_id'] for d in deliveries] == [team.users['owner'].id]
    replay = await team.service.replay(team.actor('owner'), c['id'], after=0)
    assert [e['kind'] for e in replay['items']] == ['chat_group_created', 'draft_changed']
    assert 'private' not in str(replay)


async def test_claim_is_exclusive_and_stale_lease_cannot_ack(team):
    c = await room(team)
    await send(team, c['id'])
    a, b = await asyncio.gather(team.db.chat_claim_deliveries(limit=3), team.db.chat_claim_deliveries(limit=3))
    assert len(a) == len(b) == 3 and not {d['id'] for d in a} & {d['id'] for d in b}
    old = a[0]
    await team.db._db.execute("UPDATE chat_event_deliveries SET lease_until = clock_timestamp() - interval '1 second' WHERE id = ?", (old['id'],))
    new = next(d for d in await team.db.chat_claim_deliveries() if d['id'] == old['id'])
    assert new['lease_token'] != old['lease_token'] and new['attempts'] == 2
    assert not await team.db.chat_finish_delivery(old, success=True)
    assert await team.db.chat_finish_delivery(new, success=True)
    assert not await team.db.chat_finish_delivery(new, success=True)


async def test_publish_failure_retries_without_losing_durable_replay(team):
    class Signals:
        available = False
        calls = []
        async def publish_many(self, recipients):
            if not self.available:
                raise ConnectionError()
            self.calls.extend(recipients)
    c = await room(team)
    await send(team, c['id'])
    signals = Signals()
    runtime = ChatRuntime(team.db, signals)
    await runtime.drain_once()
    pending = await rows(team, 'chat_event_deliveries')
    assert all(d['delivered_at'] is None and d['attempts'] == 1 and d['lease_token'] is None for d in pending)
    assert (await team.service.replay(team.actor('peer'), c['id'], after=1))['items']
    signals.available = True
    await team.db._db.execute('UPDATE chat_event_deliveries SET available_at = clock_timestamp()')
    await runtime.drain_once()
    assert all(d['delivered_at'] for d in await rows(team, 'chat_event_deliveries'))
    assert len(signals.calls) == 4  # coalesced per recipient


async def test_coalesced_backpressure_and_connection_budget(team):
    runtime = ChatRuntime(team.db, None, max_connections=2, per_user=1)
    async with runtime.register(team.actor('owner')) as wake:
        with pytest.raises(OverflowError):
            async with runtime.register(team.actor('owner')):
                pass
        for _ in range(10000):
            runtime.wake(team.account.id, team.users['owner'].id)
        assert wake.is_set()
        wake.clear()
        assert not wake.is_set()
    assert not runtime.connections


async def test_realtime_migration_is_repeatable_with_existing_private_events(team):
    from adapters.storage.chat_schema import migrate_chat_realtime
    c = await room(team)
    await team.service.draft(team.actor('owner'), c['id'], expected_version=0, body='preserved')
    before = await rows(team, 'chat_events')
    await migrate_chat_realtime(team.db._db)
    await migrate_chat_realtime(team.db._db)
    assert await rows(team, 'chat_events') == before
    assert (await team.service.draft(team.actor('owner'), c['id']))['body'] == 'preserved'
    await team.service.personal_state(team.actor('owner'), c['id'], changes={'muted': True})
    private = (await team.service.replay(team.actor('owner'), c['id'], after=2))['items']
    assert [e['kind'] for e in private] == ['state_changed']
    assert not (await team.service.replay(team.actor('peer'), c['id'], after=2))['items']
