"""In-loop ASGI WebSocket peer: real app/middleware, same isolated DB loop."""
import asyncio
from contextlib import asynccontextmanager, suppress
import json


class Peer:
    def __init__(self, outgoing, incoming):
        self.outgoing, self.incoming = outgoing, incoming

    async def frame(self):
        return await asyncio.wait_for(self.outgoing.get(), 5)

    async def receive(self, kind=None):
        while True:
            frame = await self.frame()
            if frame['type'] == 'websocket.close':
                return frame
            data = json.loads(frame['text'])
            if kind is None or data['type'] == kind:
                return data

    async def subscribe(self, cid, after=0):
        await self.incoming.put({'type': 'websocket.receive', 'text': json.dumps({
            'type': 'subscribe', 'conversations': [{'id': cid, 'after': after}]})})


@asynccontextmanager
async def socket_peer(app, token='', *, origin='https://dash.4truck.us', path='/api/chat/ws', query='', extra_headers=()):
    outgoing, incoming = asyncio.Queue(), asyncio.Queue()
    headers = [(b'host', b'test')]
    if token:
        headers.append((b'cookie', ('auth_token=' + token).encode()))
    if origin is not None:
        headers.append((b'origin', origin.encode()))
    headers.extend(extra_headers)
    scope = {'type': 'websocket', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'scheme': 'ws', 'path': path, 'raw_path': path.encode(), 'query_string': query.encode(),
             'root_path': '', 'headers': headers, 'client': ('127.0.0.1', 123), 'server': ('test', 80),
             'subprotocols': [], 'state': {}}
    task = asyncio.create_task(app(scope, incoming.get, outgoing.put))
    await incoming.put({'type': 'websocket.connect'})
    try:
        yield Peer(outgoing, incoming)
    finally:
        await incoming.put({'type': 'websocket.disconnect', 'code': 1000})
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
