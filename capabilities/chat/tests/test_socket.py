"""Production ASGI socket auth, idle enforcement and gap repair with real JWTs."""
import asyncio
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from capabilities.chat.realtime import ChatRuntime
from capabilities.chat.tests._socket import socket_peer
from capabilities.chat.tests.test_messages import room, send
from capabilities.permissions.roles import invalidate_permissions_cache
from interfaces.api import auth, deps
from interfaces.api.app import create_api

pytestmark = pytest.mark.security


def token(team, name='peer', **claims):
    u = team.users[name]
    return auth.create_jwt(u.telegram_id, team.account.id, u.role.value, user_id=u.id, **claims)


@pytest_asyncio.fixture
async def app(team, monkeypatch):
    monkeypatch.setenv('BILLING_ENFORCEMENT_ENABLED', '0')
    monkeypatch.setenv('QUARANTINE_ENFORCEMENT_ENABLED', '0')
    monkeypatch.setattr(deps, '_fire_heartbeats', lambda _: None)
    app = create_api()
    app.state.chat_runtime = ChatRuntime(team.db, None, poll_seconds=.05)
    yield app
    assert not app.state.chat_runtime.connections


async def test_browser_handshake_refuses_unsafe_credentials_and_origins(app, team):
    valid = token(team)
    scoped = auth.jwt.encode({**auth.decode_jwt(valid), 'scope': ['can_view_chat']}, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    cases = [('', {}), (valid, {'origin': None}), (valid, {'origin': 'https://evil.test'}),
             (valid, {'query': 'token=' + valid}),
             (valid, {'extra_headers': [(b'authorization', ('Bearer '+valid).encode())]}),
             (token(team, aud='extension'), {}), (token(team, aud='unknown'), {}),
             (scoped, {})]
    for credential, options in cases:
        async with socket_peer(app, credential, **options) as peer:
            assert (await peer.frame())['type'] == 'websocket.close'
    for path in ('/api/chat/ws', '/api/v1/chat/ws'):
        async with socket_peer(app, valid, path=path) as peer:
            assert (await peer.frame())['type'] == 'websocket.accept'
            assert (await peer.receive('ready'))['protocol'] == 1


async def test_the_socket_reads_under_the_actors_account_scope(app, team, monkeypatch):
    """Row-level security reads the account from the task's scope.  An HTTP
    route enters it in get_tenant_db; a socket has no dependency chain and
    must enter it itself — before the first tenant read, or the day
    ENABLE_RLS is set every read on this path returns nothing and the
    socket goes quiet with no error."""
    from adapters.storage.pg_adapter import _current_account_id
    seen = []
    original = team.db.chat_sync_revision

    async def spy(aid, uid):
        seen.append(_current_account_id.get())
        return await original(aid, uid)
    monkeypatch.setattr(team.db, 'chat_sync_revision', spy)
    async with socket_peer(app, token(team)) as peer:
        assert (await peer.frame())['type'] == 'websocket.accept'
        await peer.receive('ready')
    assert seen, "the socket never read the tenant's sync state"
    assert set(seen) == {team.account.id}, seen


async def test_periodic_replay_repairs_lost_hints_and_http_uses_same_cursor(app, team):
    c = await room(team)
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.subscribe(c['id'], 1)
        initial = await peer.receive('events')
        assert initial['cursor'] == 1
        message = await send(team, c['id'])  # no Redis and no local wake
        live = await peer.receive('events')
        assert live['items'][0]['message']['id'] == message['id']
        saved = live['cursor']
    offline = await send(team, c['id'])
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.subscribe(c['id'], saved)
        replay = await peer.receive('events')
        assert replay['items'][0]['message']['id'] == offline['id']
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(f"/api/chat/conversations/{c['id']}/events?after={saved}",
                                    headers={'Authorization': 'Bearer '+token(team)})
        assert response.status_code == 200, response.text
        assert response.json() == {k: v for k, v in replay.items() if k != 'type'}
        assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('revoke', ['grant', 'session', 'inactive', 'account', 'quarantine', 'billing', 'database'])
async def test_idle_socket_is_closed_when_access_disappears(app, team, monkeypatch, revoke):
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.receive('sync')
        if revoke == 'grant':
            await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
            invalidate_permissions_cache(team.account.id)
        elif revoke == 'session':
            await team.db._db.execute('UPDATE users SET auth_version = auth_version + 1 WHERE id = ?', (team.users['peer'].id,))
        elif revoke == 'inactive':
            await team.db._db.execute('UPDATE users SET is_active = 0 WHERE id = ?', (team.users['peer'].id,))
        elif revoke == 'account':
            await team.db._db.execute('UPDATE accounts SET is_active = 0 WHERE id = ?', (team.account.id,))
        elif revoke == 'quarantine':
            monkeypatch.setenv('QUARANTINE_ENFORCEMENT_ENABLED', '1')
            await team.db._db.execute("UPDATE users SET security = 'quarantined' WHERE id = ?", (team.users['peer'].id,))
        elif revoke == 'billing':
            monkeypatch.setenv('BILLING_ENFORCEMENT_ENABLED', '1')
            monkeypatch.setattr(team.db, 'get_subscription', AsyncMock(return_value={'status': 'canceled'}))
        else:
            monkeypatch.setattr(team.db, 'chat_sync_revision', AsyncMock(side_effect=ConnectionError('offline')))
        close = await peer.receive()
        assert close['type'] == 'websocket.close' and close['code'] in (1008, 1011)


async def test_audience_removal_invalidates_subscription_without_leaking(app, team):
    c = await room(team)
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.subscribe(c['id'], 1)
        await peer.receive('events')
        await team.service.update_group(team.actor('fleet'), c['id'], expected_version=1, changes={'role_keys': ['fleet']})
        assert (await peer.receive('removed'))['conversation_id'] == c['id']
        await send(team, c['id'], body='private after removal')
        await asyncio.sleep(.1)
        while not peer.outgoing.empty():
            assert 'private after removal' not in str(await peer.frame())


async def test_socket_cannot_subscribe_to_other_role_room_or_send_writes(app, team):
    c = await room(team)
    async with socket_peer(app, token(team, 'outsider')) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.subscribe(c['id'])
        assert (await peer.receive('removed'))['conversation_id'] == c['id']
        await peer.incoming.put({'type': 'websocket.receive', 'text': '{"type":"send","body":"injected"}'})
        assert (await peer.receive())['type'] == 'websocket.close'
    assert not (await team.service.list_messages(team.actor('fleet'), c['id']))['items']


async def test_token_expiry_closes_an_already_open_socket(app, team):
    import time
    payload = {**auth.decode_jwt(token(team)), 'exp': int(time.time()) + 2}
    credential = auth.jwt.encode(payload, auth.JWT_SECRET, algorithm=auth.JWT_ALGORITHM)
    async with socket_peer(app, credential) as peer:
        assert (await peer.frame())['type'] == 'websocket.accept'
        await peer.receive('ready')
        await peer.receive('sync')
        close = await peer.receive()
        assert close['type'] == 'websocket.close' and close['code'] == 1008


async def test_saved_session_revocation_is_enforced_without_redis(app, team):
    from datetime import datetime, timedelta, timezone
    credential = token(team)
    payload = auth.decode_jwt(credential)
    now = datetime.now(timezone.utc)
    await team.db.create_user_session(user_id=team.users['peer'].id, jti=payload['jti'], device_label='test',
        user_agent='test', ip='127.0.0.1', created_at=now.isoformat(), last_seen=now.isoformat(),
        expires_at=(now+timedelta(hours=1)).isoformat())
    async with socket_peer(app, credential) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.receive('sync')
        await team.db._db.execute('UPDATE user_sessions SET revoked_at = ? WHERE jti = ?', (now.isoformat(), payload['jti']))
        assert (await peer.receive())['code'] == 1008


async def test_slow_socket_write_has_a_deadline(monkeypatch):
    from capabilities.chat.router import _socket_send
    class Slow:
        async def send_json(self, value):
            await asyncio.Event().wait()
    # Exercise the actual five-second production budget.
    with pytest.raises(asyncio.TimeoutError):
        await _socket_send(Slow(), {'type': 'events'})


async def test_binary_oversized_and_excess_subscriptions_are_rejected():
    from capabilities.chat.router import _subscriptions
    from uuid import uuid4
    import json
    for raw in (None, ' ' * 4097, json.dumps({'type': 'subscribe', 'conversations': [
            {'id': str(uuid4()), 'after': 0} for _ in range(21)]}),
            json.dumps({'type': 'subscribe', 'conversations': [{'id': str(uuid4()), 'after': True}]})):
        with pytest.raises((ValueError, TypeError)):
            _subscriptions(raw)


async def test_unsubscribed_room_wakes_inbox_without_content(app, team):
    c = await room(team)
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        original = await peer.receive('sync')
        await send(team, c['id'], body='unsubscribed content')
        update = await peer.receive('sync')
        assert update['revision'] > original['revision']
        assert set(update) == {'type', 'revision'}


async def test_dm_peer_grant_loss_updates_caller_actions_without_message(app, team):
    c = await team.service.ensure_direct(team.actor('fleet'), team.users['peer'].id)
    async with socket_peer(app, token(team, 'fleet')) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.subscribe(c['id'], 0)
        initial = await peer.receive('events')
        assert 'send' in initial['caller']['actions']
        await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
        invalidate_permissions_cache(team.account.id)
        changed = await peer.receive('events')
        assert changed['cursor'] == initial['cursor'] and changed['items'] == []
        assert 'send' not in changed['caller']['actions']


async def test_auth_database_outage_closes_socket_as_retryable(app, team, monkeypatch):
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.receive('sync')
        monkeypatch.setattr(team.db, 'get_user_auth_state', AsyncMock(side_effect=ConnectionError('unavailable')))
        assert (await peer.receive())['code'] == 1011


async def test_duplicate_hints_do_not_postpone_periodic_grant_enforcement(app, team):
    async with socket_peer(app, token(team)) as peer:
        await peer.frame()
        await peer.receive('ready')
        await peer.receive('sync')
        async def noisy_broker():
            while True:
                app.state.chat_runtime.wake(team.account.id, team.users['peer'].id)
                await asyncio.sleep(.005)
        noise = asyncio.create_task(noisy_broker())
        try:
            await team.db.set_role_permissions(team.account.id, 'dispatcher', {'can_view_chat': False})
            invalidate_permissions_cache(team.account.id)
            assert (await peer.receive())['code'] == 1008
        finally:
            noise.cancel()
            await asyncio.gather(noise, return_exceptions=True)
