"""HTTP adapter for Chat. Identity/billing/quarantine stay in the API layer.

Mounted under both API prefixes. HTTP writes and visibility-filtered replay
share the same service; WebSockets carry replay batches, never client writes.
"""
import asyncio
from contextlib import suppress
import json
import time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.routing import APIRoute
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from interfaces.api.deps import get_tenant_db, require_permission, resolve_user_id
from interfaces.api.rate_limit import limiter
from .models import Actor, ChatError
from .service import ChatService


class ChatRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except ChatError as exc:
                status = (404 if exc.code == 'not_found' else 403 if exc.code == 'forbidden' else
                          429 if exc.code == 'group_limit' else 409 if exc.code in {
                              'version_conflict', 'idempotency_conflict', 'identity_changed',
                              'owner_still_eligible', 'owner_unavailable', 'owner_ineligible',
                          } else 400)
                raise HTTPException(status_code=status, detail={'code': exc.code}) from None
        return handle


router = APIRouter(prefix='/chat', tags=['chat'], route_class=ChatRoute)


def _rate_key(request: Request) -> str:
    # require_permission resolves/stamps identity before the handler's limiter.
    return f"chat:{request.state.account_id}:{request.state.user_id}"


async def context(user: dict = Depends(require_permission('can_view_chat')), db=Depends(get_tenant_db)):
    uid = await resolve_user_id(user)
    if uid <= 0:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return ChatService(db), Actor(user['account_id'], uid)


Context = Annotated[tuple, Depends(context)]
PositiveID = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
PathID = Annotated[int, Path(gt=0, le=2**63 - 1)]
Version = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class GroupCreate(Input):
    title: str = Field(max_length=160)
    description: str = Field(default='', max_length=600)
    kind: Literal['account', 'roles'] = 'account'
    role_keys: list[str] = Field(default_factory=list, max_length=9)
    posting_mode: Literal['everyone', 'admins'] = 'everyone'
    new_member_history: Literal['all', 'since_join'] = 'all'


class GroupChanges(Input):
    title: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=600)
    posting_mode: Literal['everyone', 'admins'] | None = None
    new_member_history: Literal['all', 'since_join'] | None = None
    role_keys: list[str] | None = Field(default=None, max_length=9)
    archived: StrictBool | None = None


class GroupUpdate(Input):
    expected_version: Version
    changes: GroupChanges


class DirectCreate(Input):
    other_user_id: PositiveID


class AdminUpdate(Input):
    expected_version: Version
    actions: list[str] = Field(max_length=6)


class OwnershipUpdate(Input):
    expected_version: Version
    new_owner_id: PositiveID


class Send(Input):
    client_message_id: str = Field(min_length=1, max_length=128)
    draft_version: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)] | None = None
    body: str = Field(min_length=1, max_length=8000)
    reply_to_id: PositiveID | None = None
    mentions: list[PositiveID] = Field(default_factory=list, max_length=50)


class Edit(Input):
    expected_version: Version
    body: str = Field(min_length=1, max_length=8000)
    mentions: list[PositiveID] = Field(default_factory=list, max_length=50)


class Read(Input):
    through_message_seq: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]


class PersonalState(Input):
    muted: StrictBool | None = None
    pinned: StrictBool | None = None
    archived: StrictBool | None = None


class Draft(Input):
    expected_version: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
    body: str = Field(max_length=8000)
    reply_to_id: PositiveID | None = None


@router.get('/bootstrap')
@limiter.limit('120/minute', key_func=_rate_key)
async def bootstrap(request: Request, ctx: Context, after: UUID | None = None, limit: int = Query(50, ge=1, le=100)):
    service, actor = ctx
    return await service.bootstrap(actor, after=str(after) if after else '', limit=limit)


@router.get('/people')
@limiter.limit('120/minute', key_func=_rate_key)
async def people(request: Request, ctx: Context, after: int = Query(0, ge=0, le=2**63 - 1),
                 limit: int = Query(50, ge=1, le=100), q: str = Query('', max_length=200), conversation_id: UUID | None = None,
                 message_id: int | None = Query(None, ge=1, le=2**63 - 1)):
    service, actor = ctx
    return await service.people(actor, after=after, limit=limit, query=q,
                                conversation_id=str(conversation_id) if conversation_id else None, message_id=message_id)


@router.get('/recovery')
@limiter.limit('30/minute', key_func=_rate_key)
async def recovery(request: Request, ctx: Context, after: UUID | None = None, limit: int = Query(50, ge=1, le=100)):
    service, actor = ctx
    return await service.recovery_groups(actor, after=str(after) if after else '', limit=limit)


@router.post('/conversations')
@limiter.limit('30/minute', key_func=_rate_key)
async def create_group(request: Request, payload: GroupCreate, ctx: Context):
    service, actor = ctx
    return await service.create_group(actor, **payload.model_dump())


@router.post('/direct')
@limiter.limit('30/minute', key_func=_rate_key)
async def direct(request: Request, payload: DirectCreate, ctx: Context):
    service, actor = ctx
    return await service.ensure_direct(actor, payload.other_user_id)


@router.get('/conversations/{cid}')
@limiter.limit('120/minute', key_func=_rate_key)
async def conversation(request: Request, cid: UUID, ctx: Context):
    service, actor = ctx
    return await service.get_conversation(actor, str(cid))


@router.patch('/conversations/{cid}')
@limiter.limit('30/minute', key_func=_rate_key)
async def update_group(request: Request, cid: UUID, payload: GroupUpdate, ctx: Context):
    service, actor = ctx
    return await service.update_group(actor, str(cid), expected_version=payload.expected_version,
                                      changes=payload.changes.model_dump(exclude_unset=True))


@router.get('/conversations/{cid}/members')
@limiter.limit('120/minute', key_func=_rate_key)
async def members(request: Request, cid: UUID, ctx: Context, after: int = Query(0, ge=0, le=2**63 - 1),
                  limit: int = Query(50, ge=1, le=100), q: str = Query('', max_length=200)):
    service, actor = ctx
    return await service.people(actor, conversation_id=str(cid), after=after, limit=limit, query=q, group_roles=True)


@router.get('/conversations/{cid}/admins')
@limiter.limit('60/minute', key_func=_rate_key)
async def admins(request: Request, cid: UUID, ctx: Context):
    service, actor = ctx
    return await service.list_admins(actor, str(cid))


@router.put('/conversations/{cid}/admins/{uid}')
@limiter.limit('30/minute', key_func=_rate_key)
async def set_admin(request: Request, cid: UUID, uid: PathID, payload: AdminUpdate, ctx: Context):
    service, actor = ctx
    return await service.set_admin(actor, str(cid), uid, **payload.model_dump())


@router.delete('/conversations/{cid}/admins/{uid}')
@limiter.limit('30/minute', key_func=_rate_key)
async def remove_admin(request: Request, cid: UUID, uid: PathID, ctx: Context, expected_version: int = Query(ge=1, le=2**63 - 1)):
    service, actor = ctx
    return await service.set_admin(actor, str(cid), uid, expected_version=expected_version, actions=None)


@router.post('/conversations/{cid}/transfer-ownership')
@limiter.limit('15/minute', key_func=_rate_key)
async def transfer(request: Request, cid: UUID, payload: OwnershipUpdate, ctx: Context):
    service, actor = ctx
    return await service.transfer_ownership(actor, str(cid), **payload.model_dump())


@router.post('/conversations/{cid}/recover-ownership')
@limiter.limit('15/minute', key_func=_rate_key)
async def recover(request: Request, cid: UUID, payload: OwnershipUpdate, ctx: Context):
    service, actor = ctx
    return await service.transfer_ownership(actor, str(cid), **payload.model_dump(), recovery=True)


@router.get('/conversations/{cid}/messages')
@limiter.limit('120/minute', key_func=_rate_key)
async def history(request: Request, cid: UUID, ctx: Context, before: int | None = Query(None, ge=1, le=2**63 - 1),
                  limit: int = Query(50, ge=1, le=100)):
    service, actor = ctx
    return await service.list_messages(actor, str(cid), before=before, limit=limit)


@router.post('/conversations/{cid}/messages')
@limiter.limit('60/minute', key_func=_rate_key)
async def send(request: Request, cid: UUID, payload: Send, ctx: Context):
    service, actor = ctx
    return await service.send_message(actor, str(cid), **payload.model_dump())


@router.get('/conversations/{cid}/messages/{mid}')
@limiter.limit('120/minute', key_func=_rate_key)
async def message(request: Request, cid: UUID, mid: PathID, ctx: Context):
    service, actor = ctx
    return await service.get_message(actor, str(cid), mid)


@router.patch('/conversations/{cid}/messages/{mid}')
@limiter.limit('60/minute', key_func=_rate_key)
async def edit(request: Request, cid: UUID, mid: PathID, payload: Edit, ctx: Context):
    service, actor = ctx
    return await service.edit_message(actor, str(cid), mid, **payload.model_dump())


@router.delete('/conversations/{cid}/messages/{mid}')
@limiter.limit('60/minute', key_func=_rate_key)
async def delete(request: Request, cid: UUID, mid: PathID, ctx: Context, expected_version: int = Query(ge=1, le=2**63 - 1)):
    service, actor = ctx
    return await service.delete_message(actor, str(cid), mid, expected_version=expected_version)


@router.get('/conversations/{cid}/search')
@limiter.limit('60/minute', key_func=_rate_key)
async def search(request: Request, cid: UUID, ctx: Context, q: str = Query(min_length=1, max_length=200),
                 before: int | None = Query(None, ge=1, le=2**63 - 1), limit: int = Query(50, ge=1, le=100)):
    service, actor = ctx
    return await service.list_messages(actor, str(cid), before=before, limit=limit, query=q)


@router.get('/conversations/{cid}/pins')
@limiter.limit('120/minute', key_func=_rate_key)
async def pins(request: Request, cid: UUID, ctx: Context, before: int | None = Query(None, ge=1, le=2**63 - 1),
               limit: int = Query(50, ge=1, le=100)):
    service, actor = ctx
    return await service.list_messages(actor, str(cid), before=before, limit=limit, pinned=True)


@router.put('/conversations/{cid}/pins/{mid}')
@limiter.limit('30/minute', key_func=_rate_key)
async def pin(request: Request, cid: UUID, mid: PathID, ctx: Context, expected_version: int = Query(ge=1, le=2**63 - 1)):
    service, actor = ctx
    return await service.pin_message(actor, str(cid), mid, expected_version=expected_version)


@router.delete('/conversations/{cid}/pins/{mid}')
@limiter.limit('30/minute', key_func=_rate_key)
async def unpin(request: Request, cid: UUID, mid: PathID, ctx: Context, expected_version: int = Query(ge=1, le=2**63 - 1)):
    service, actor = ctx
    return await service.pin_message(actor, str(cid), mid, expected_version=expected_version, pinned=False)


@router.put('/conversations/{cid}/read')
@limiter.limit('120/minute', key_func=_rate_key)
async def read(request: Request, cid: UUID, payload: Read, ctx: Context):
    service, actor = ctx
    return await service.read_messages(actor, str(cid), **payload.model_dump())


@router.get('/conversations/{cid}/state')
@limiter.limit('120/minute', key_func=_rate_key)
async def state(request: Request, cid: UUID, ctx: Context):
    service, actor = ctx
    return await service.personal_state(actor, str(cid))


@router.patch('/conversations/{cid}/state')
@limiter.limit('60/minute', key_func=_rate_key)
async def update_state(request: Request, cid: UUID, payload: PersonalState, ctx: Context):
    service, actor = ctx
    return await service.personal_state(actor, str(cid), changes=payload.model_dump(exclude_unset=True))


@router.get('/conversations/{cid}/draft')
@limiter.limit('120/minute', key_func=_rate_key)
async def draft(request: Request, cid: UUID, ctx: Context):
    service, actor = ctx
    return await service.draft(actor, str(cid))


@router.put('/conversations/{cid}/draft')
@limiter.limit('120/minute', key_func=_rate_key)
async def save_draft(request: Request, cid: UUID, payload: Draft, ctx: Context):
    service, actor = ctx
    return await service.draft(actor, str(cid), **payload.model_dump())


@router.get('/sync')
@limiter.limit('120/minute', key_func=_rate_key)
async def sync_status(request: Request, ctx: Context):
    service, actor = ctx
    from interfaces.api.chat_security import check_chat_holds
    await check_chat_holds(service.db, actor)
    return {'revision': await service.sync_revision(actor), 'poll_after_ms': 2000}


@router.get('/conversations/{conversation_id}/events')
@limiter.limit('120/minute', key_func=_rate_key)
async def replay_events(request: Request, conversation_id: UUID, ctx: Context,
                        after: int | None = Query(default=None, ge=0, le=2**63 - 1),
                        limit: int = Query(default=50, ge=1, le=100)):
    service, actor = ctx
    from interfaces.api.chat_security import check_chat_holds
    await check_chat_holds(service.db, actor)
    return await service.replay(actor, str(conversation_id), after=after, limit=limit)


async def _socket_send(socket, value):
    # Slow consumer has a bounded write budget and no unbounded message queue.
    await asyncio.wait_for(socket.send_json(jsonable_encoder(value)), 5)


def _subscriptions(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 4096:
        raise ValueError('Invalid frame')
    command = json.loads(raw)
    if not isinstance(command, dict) or command.get('type') != 'subscribe' or set(command) != {'type', 'conversations'}:
        raise ValueError('Invalid command')
    values = command['conversations']
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError('Subscription limit')
    subscriptions = {}
    for item in values:
        if not isinstance(item, dict) or not {'id'} <= item.keys() or not item.keys() <= {'id', 'after'}:
            raise ValueError('Invalid subscription')
        if not isinstance(item['id'], str):
            raise ValueError('Invalid conversation')
        cid = str(UUID(item['id']))
        after = item.get('after')
        if after is not None and (type(after) is not int or not 0 <= after <= 2**63 - 1):
            raise ValueError('Invalid cursor')
        if cid in subscriptions:
            raise ValueError('Duplicate conversation')
        subscriptions[cid] = after
    return subscriptions


@router.websocket('/ws')
async def chat_socket(socket: WebSocket):
    from interfaces.api.chat_security import authenticate_chat_socket
    runtime = getattr(socket.app.state, 'chat_runtime', None)
    if runtime is None:
        await socket.close(code=1013)
        return
    receiver = waiter = None
    try:
        actor = await authenticate_chat_socket(socket, runtime.db)
        # Row-level security reads the account from the task's scope. An HTTP
        # route enters it through get_tenant_db; a socket has no dependency
        # chain, so it enters the scope itself, once, for its whole life —
        # nothing is pinned, the pool stamps the GUC on each acquire.
        async with runtime.db.with_account(actor.account_id):
            service = ChatService(runtime.db)
            await service.sync_revision(actor)  # Chat grant/account gate before accept
            async with runtime.register(actor) as wake:
                # Register BEFORE any catch-up; live hints arriving during replay
                # remain set. Periodic DB replay repairs missed pub/sub subscriptions.
                await socket.accept()
                await _socket_send(socket, {'type': 'ready', 'protocol': 1, 'poll_after_ms': 2000,
                                            'max_subscriptions': 20})
                subscriptions, scopes = {}, {}
                revision, heartbeat = None, time.monotonic()
                checked_at, force_check = 0.0, True
                command_window, commands = time.monotonic(), 0
                receiver = asyncio.create_task(socket.receive())
                while True:
                    wake.clear()
                    now = time.monotonic()
                    candidate_revision = await runtime.db.chat_sync_revision(actor.account_id, actor.user_id)
                    reconcile = force_check or now - checked_at >= runtime.poll_seconds or candidate_revision != revision
                    if reconcile:
                        if await authenticate_chat_socket(socket, runtime.db) != actor:
                            raise HTTPException(401, 'Session changed')
                        current_revision = await service.sync_revision(actor)
                        checked_at, force_check = time.monotonic(), False
                    else:
                        current_revision = revision
                    if current_revision != revision:
                        await _socket_send(socket, {'type': 'sync', 'revision': current_revision})
                        revision = current_revision
                    more = False
                    for cid, cursor in list(subscriptions.items()) if reconcile else ():
                        # Revalidate transport policy for each batch as well as idle
                        # passes; a long catch-up cannot outlive its session/hold.
                        await authenticate_chat_socket(socket, runtime.db)
                        try:
                            batch = await service.replay(actor, cid, after=cursor)
                        except ChatError as exc:
                            if exc.code != 'not_found':
                                raise
                            await _socket_send(socket, {'type': 'removed', 'conversation_id': cid})
                            del subscriptions[cid]
                            scopes.pop(cid, None)
                            continue
                        access_state = (batch['scope'], batch['caller'])
                        scope_changed = scopes.get(cid) != access_state
                        if batch['cursor'] != cursor or batch['resync_required'] or scope_changed:
                            await _socket_send(socket, {'type': 'events', **batch})
                            subscriptions[cid], scopes[cid] = batch['cursor'], access_state
                        more = more or batch['has_more']
                    if time.monotonic() - heartbeat >= 20:
                        await _socket_send(socket, {'type': 'heartbeat'})
                        heartbeat = time.monotonic()
                    if more:
                        force_check = True
                        wake.set()
                    waiter = asyncio.create_task(wake.wait())
                    done, _ = await asyncio.wait((receiver, waiter), timeout=max(0, runtime.poll_seconds - (time.monotonic() - checked_at)),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    waiter.cancel()
                    with suppress(asyncio.CancelledError):
                        await waiter
                    waiter = None
                    if receiver in done:
                        frame = receiver.result()
                        if frame['type'] == 'websocket.disconnect':
                            return
                        now = time.monotonic()
                        if now - command_window >= 10:
                            command_window, commands = now, 0
                        commands += 1
                        if commands > 10:
                            raise OverflowError('Command limit')
                        subscriptions = _subscriptions(frame.get('text'))
                        scopes = {}
                        force_check = True
                        receiver = asyncio.create_task(socket.receive())
    except WebSocketDisconnect:
        # The peer hung up; there is nobody left to tell.
        pass
    except HTTPException as exc:
        with suppress(RuntimeError, OSError):
            await socket.close(code=1011 if exc.status_code >= 500 else 1008)
    except ChatError:
        with suppress(RuntimeError, OSError):
            await socket.close(code=1008)
    except (ValueError, TypeError, KeyError):
        with suppress(RuntimeError, OSError):
            await socket.close(code=1008)
    except (OverflowError, asyncio.TimeoutError):
        with suppress(RuntimeError, OSError):
            await socket.close(code=1013, reason='Reconnect and replay')
    except Exception:
        # No permissive stream on DB/policy outages. Never log token/body/frame.
        with suppress(RuntimeError, OSError):
            await socket.close(code=1011, reason='Verification unavailable')
    finally:
        for task in (receiver, waiter):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                    await task
