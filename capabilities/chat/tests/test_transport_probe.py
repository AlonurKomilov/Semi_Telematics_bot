"""Two OS processes + real TCP WebSockets + isolated Postgres and Redis.

The optional capacity probe is intentionally opt-in (300s/100 connections).
Never points at the running application's database, Redis, or listener.
"""
import asyncio
from contextlib import AsyncExitStack
import json
import multiprocessing
import os
from pathlib import Path
import socket
import time

import httpx
import pytest
import pytest_asyncio
import websockets
from testcontainers.core.container import DockerContainer

from capabilities.chat.tests._worker import serve
from capabilities.chat.tests.test_messages import room, send
from capabilities.chat.tests.test_socket import token


@pytest_asyncio.fixture
async def processes(team):
    from adapters.storage import core
    redis = DockerContainer('redis:7-alpine').with_exposed_ports(6379)
    await asyncio.to_thread(redis.start)
    redis_url = f'redis://127.0.0.1:{redis.get_exposed_port(6379)}/0'
    children = []
    async def launch():
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', 0))
        listener.listen(512)
        port = listener.getsockname()[1]
        ctx = multiprocessing.get_context('spawn')
        parent, child = ctx.Pipe(duplex=False)
        process = ctx.Process(target=serve, args=(core._DATABASE_URL, redis_url, listener, child))
        process.start()
        children.append(process)
        listener.close()
        try:
            assert await asyncio.to_thread(parent.poll, 30), 'Test API worker startup timed out'
            assert parent.recv()
        finally:
            parent.close()
            child.close()
        return process, f'http://127.0.0.1:{port}'
    try:
        a, b = await launch(), await launch()
        yield a, b, redis, launch
    finally:
        for process in children:
            if process.is_alive():
                process.terminate()
        for process in children:
            await asyncio.to_thread(process.join, 10)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 5)
        await asyncio.to_thread(redis.stop)


async def connect(base, credential, cid, cursor):
    ws = await websockets.connect(base.replace('http:', 'ws:') + '/api/chat/ws',
        origin='https://dash.4truck.us', additional_headers={'Cookie': 'auth_token='+credential},
        open_timeout=10, max_queue=16)
    assert json.loads(await ws.recv())['type'] == 'ready'
    await ws.send(json.dumps({'type': 'subscribe', 'conversations': [{'id': cid, 'after': cursor}]}))
    return ws


async def event_batch(ws, *, message_id=None):
    async with asyncio.timeout(12):
        while True:
            value = json.loads(await ws.recv())
            if value['type'] == 'events' and (message_id is None or
                any(e.get('message', {}).get('id') == message_id for e in value['items'])):
                return value


async def test_two_workers_restart_and_redis_outage(processes, team):
    (pa, a), (pb, b), redis, launch = processes
    c = await room(team)
    async with httpx.AsyncClient(base_url=a) as http:
        ws = await connect(b, token(team), c['id'], 1)
        try:
            await event_batch(ws)
            live_start = time.perf_counter()
            response = await http.post(f"/api/chat/conversations/{c['id']}/messages",
                headers={'Authorization': 'Bearer '+token(team, 'fleet')},
                json={'client_message_id': 'cross-worker', 'body': 'across workers'})
            assert response.status_code == 200, response.text
            message = response.json()['message']
            live = await event_batch(ws, message_id=message['id'])
            assert time.perf_counter() - live_start < 1.5  # faster than the 2s fallback scan
            saved = live['cursor']
            # Pause ONLY this test's Redis container. Durable polling still runs.
            container = redis.get_wrapped_container()
            await asyncio.to_thread(container.pause)
            try:
                offline = await send(team, c['id'], body='during redis outage')
                fallback = await event_batch(ws, message_id=offline['id'])
                assert fallback['cursor'] > saved
            finally:
                await asyncio.to_thread(container.unpause)
            pb.terminate()
            await asyncio.to_thread(pb.join, 10)
        finally:
            await ws.close()
        restart_message = await send(team, c['id'], body='during worker restart')
        _, restarted = await launch()
        resumed = await connect(restarted, token(team), c['id'], saved)
        try:
            replay = await event_batch(resumed, message_id=restart_message['id'])
            ids = [e['message']['id'] for e in replay['items'] if 'message' in e]
            assert offline['id'] in ids and restart_message['id'] in ids
            assert len(ids) == len(set(ids))
        finally:
            await resumed.close()


@pytest.mark.skipif(os.getenv('CHAT_CAPACITY_PROBE') != '1', reason='Opt-in isolated 300s capacity probe')
async def test_capacity_100_connections_100k_history(processes, team):
    from adapters.storage import Role
    from interfaces.api.auth import create_jwt
    (_, a), (_, b), _, _ = processes
    duration = int(os.getenv('CHAT_CAPACITY_SECONDS', '300'))
    assert 10 <= duration <= 300
    clients, rooms = [], []
    for account_index in range(5):
        account = await team.db.create_account(f'Capacity {account_index}')
        await team.db.set_role_permissions(account.id, 'owner', {'can_view_chat': True})
        await team.db.set_role_permissions(account.id, 'fleet', {'can_view_chat': True})
        users = []
        for j in range(20):
            user = await team.db.create_user(990000+account_index*20+j, account.id,
                role=Role.OWNER if j == 0 else Role.FLEET, display_name=f'Test {j}')
            credential = create_jwt(user.telegram_id, account.id, user.role.value, user_id=user.id,
                                    is_primary_owner=user.is_primary_owner)
            users.append((user, credential))
        from capabilities.chat.models import Actor
        c = await team.service.create_group(Actor(account.id, users[0][0].id), title='Capacity')
        rooms.append((account, c, users))
        # Seed historical content without fake realtime deliveries: performance
        # fixture only. Event replay begins at the pre-existing group checkpoint.
        await team.db._db.execute(
            "INSERT INTO chat_messages(account_id, conversation_id, author_id, client_message_id, message_seq, body) "
            "SELECT ?, ?, ?, 'seed-' || n, n, CASE WHEN n=1 THEN 'rare needle' ELSE 'historical ' || n END "
            "FROM generate_series(1, 20000) n RETURNING id", (account.id, c['id'], users[0][0].id))
        await team.db._db.execute('UPDATE chat_conversations SET message_seq = 20000 WHERE id = ?', (c['id'],))
        for j, (_, credential) in enumerate(users):
            clients.append((a if j % 2 else b, credential, c['id'], account_index))
    Path('/tmp/chat-p3-capacity-progress.json').write_text(json.dumps({'stage': 'seeded', 'messages': 100000}))
    started, deliveries, send_ms, live_ms, faults = {}, {}, [], [], []
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=20))
        sockets = []
        for base, credential, cid, group in clients:
            ws = await connect(base, credential, cid, 1)
            stack.push_async_callback(ws.close)
            await event_batch(ws)
            sockets.append(ws)
        async def consume(index, ws, group):
            try:
                async for raw in ws:
                    value = json.loads(raw)
                    if value['type'] != 'events':
                        continue
                    assert not value['resync_required']
                    for event in value['items']:
                        msg = event.get('message', {})
                        body = msg.get('body') or ''
                        if not body.startswith('probe:'):
                            continue
                        number = int(body.split(':')[1])
                        assert number % 5 == group, 'Cross-account message'
                        seen = deliveries.setdefault(number, set())
                        if index in seen:
                            faults.append('duplicate')
                        seen.add(index)
                        live_ms.append((time.perf_counter()-started[number])*1000)
            except Exception as exc:
                faults.append(type(exc).__name__)
        readers = [asyncio.create_task(consume(i, ws, clients[i][3])) for i, ws in enumerate(sockets)]
        Path('/tmp/chat-p3-capacity-progress.json').write_text(json.dumps({'stage': 'connected', 'connections': 100}))
        begin = time.perf_counter()
        try:
            in_flight = asyncio.Semaphore(20)
            async def post_message(number):
                account, c, users = rooms[number % 5]
                _, credential = users[(number//5) % 20]
                started[number] = time.perf_counter()
                async with in_flight:
                    response = await http.post((a if number % 2 else b)+f"/api/chat/conversations/{c['id']}/messages",
                        headers={'Authorization': 'Bearer '+credential},
                        json={'client_message_id': f'probe-{number}', 'body': f'probe:{number}'})
                assert response.status_code == 200, response.text
                send_ms.append((time.perf_counter()-started[number])*1000)
                if len(send_ms) % 100 == 0:
                    Path('/tmp/chat-p3-capacity-progress.json').write_text(json.dumps({
                        'stage': 'sending', 'accepted': len(send_ms), 'send_p95_ms': round(sorted(send_ms)[int(len(send_ms)*.95)-1], 2)}))
            requests = []
            try:
                for number in range(duration*5):
                    await asyncio.sleep(max(0, begin+number*.2-time.perf_counter()))
                    requests.append(asyncio.create_task(post_message(number)))
                await asyncio.gather(*requests)
            finally:
                for task in requests:
                    task.cancel()
                await asyncio.gather(*requests, return_exceptions=True)
            async with asyncio.timeout(15):
                while sum(map(len, deliveries.values())) < duration*5*20:
                    await asyncio.sleep(.1)
            search_ms = []
            for account, c, users in rooms:
                before = time.perf_counter()
                response = await http.get(a+f"/api/chat/conversations/{c['id']}/search", params={'q': 'rare needle'},
                    headers={'Authorization': 'Bearer '+users[0][1]})
                assert response.status_code == 200 and len(response.json()['items']) == 1
                search_ms.append((time.perf_counter()-before)*1000)
            cur = await team.db._db.execute("SELECT count(*) AS count FROM chat_messages WHERE body LIKE 'probe:%'")
            committed = (await cur.fetchone())['count']
            cur = await team.db._db.execute("SELECT count(*) AS pending, COALESCE(EXTRACT(EPOCH FROM clock_timestamp()-min(e.created_at)), 0) AS age FROM chat_event_deliveries d JOIN chat_events e ON e.id=d.event_id WHERE d.delivered_at IS NULL")
            backlog = dict(await cur.fetchone())
            def p95(values):
                return round(sorted(values)[int((len(values)-1)*.95)], 2)
            report = {'workers': 2, 'accounts': 5, 'connections': 100, 'historical_messages': 100000,
                      'seconds': duration, 'rate_per_second': 5, 'accepted': len(send_ms), 'committed': committed,
                      'delivered_updates': len(live_ms), 'send_p95_ms': p95(send_ms), 'live_p95_ms': p95(live_ms),
                      'literal_search_worst_ms': round(max(search_ms), 2), 'faults': faults,
                      'outbox_pending': backlog['pending'], 'oldest_pending_seconds': float(backlog['age']),
                      'observed_rate_per_second': round(len(send_ms)/(time.perf_counter()-begin), 2),
                      'logical_cpus': os.cpu_count(),
                      'environment': 'isolated local containers on shared development host; not staging certification'}
            Path('/tmp/chat-p3-capacity.json').write_text(json.dumps(report, indent=2)+'\n')
            assert not faults and committed == duration*5
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
