"""Chat service: conversation management, messages and discovery, independent of HTTP.

Every write follows account advisory lock -> conversation row lock. The
membership triggers and message writers follow that same ordering.
The HTTP adapter is router.py; the production UI follows in P4.
"""
from contextlib import asynccontextmanager

from capabilities.permissions.roles import Role
from .access import conversation_access, recheck_actor, require_action, resolve_actor, structurally_eligible
from .models import ADMIN_ACTIONS, MAX_ACTIVE_GROUPS, Actor, ChatError
from .messages import MessageOperations
from .queries import ChatQueries
from .events import ChatEvents
from .validation import text as _text, version as _version


def _roles(values) -> list[str]:
    if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) for v in values):
        raise ChatError('invalid_audience')
    roles = sorted(set(values))
    if not roles or not set(roles) <= {r.value for r in Role}:
        raise ChatError('invalid_audience')
    return roles


def _modes(posting: str, history: str) -> None:
    if posting not in ('everyone', 'admins') or history not in ('all', 'since_join'):
        raise ChatError('invalid_settings')


def _result(conversation: dict, access) -> dict:
    # Deliberate public projection: no direct-pair IDs, internal sequence
    # counters or other members' read/draft state slip into group metadata.
    return {
        'id': conversation['id'], 'kind': conversation['kind'],
        'title': conversation['title'], 'description': conversation['description'],
        'owner_user_id': conversation['owner_user_id'], 'role_keys': conversation['role_keys'],
        'version': conversation['version'], 'archived': conversation['archived'],
        'system_key': conversation['system_key'],
        'settings': {'posting_mode': conversation['posting_mode'], 'new_member_history': conversation['new_member_history']},
        'caller': {'group_role': access.group_role, 'actions': sorted(access.actions),
                   'visible_after_message_seq': access.visible_after_message_seq},
    }


class ChatService(MessageOperations, ChatQueries, ChatEvents):
    def __init__(self, db):
        self.db = db

    @asynccontextmanager
    async def _transaction(self, actor: Actor, *, target_id: int | None = None, conversation_id: str | None = None, mentioned_ids=()):
        verified = await resolve_actor(self.db, actor)
        peer = None
        if conversation_id is not None:
            preview = await self.db.chat_get_conversation(actor.account_id, conversation_id)
            if preview and preview['kind'] == 'direct' and structurally_eligible(verified, preview):
                peer_id = (preview['direct_high_user_id'] if actor.user_id == preview['direct_low_user_id']
                           else preview['direct_low_user_id'])
                try:
                    peer = await resolve_actor(self.db, Actor(actor.account_id, peer_id))
                except ChatError:
                    pass  # Existing history stays readable; sending is unavailable.
        mentioned = {}
        for uid in mentioned_ids:
            try:
                mentioned[uid] = await resolve_actor(self.db, Actor(actor.account_id, uid))
            except ChatError:
                mentioned[uid] = None
        target = None
        if target_id is not None:
            try:
                target = await resolve_actor(self.db, Actor(actor.account_id, target_id))
            except ChatError:
                raise ChatError('not_found') from None
        async with self.db.transaction():
            await self.db.chat_lock_account(actor.account_id)
            user = await recheck_actor(self.db, verified)
            if target is not None:
                target = await recheck_actor(self.db, target)
            user['_chat_peer_granted'] = False
            if peer is not None:
                try:
                    await recheck_actor(self.db, peer)
                    user['_chat_peer_granted'] = True
                except ChatError:
                    # The peer no longer holds the grant; the flag stays
                    # False and access.py decides what that conversation
                    # still allows.
                    pass
            user['_chat_mentions'] = {}
            for uid, member in mentioned.items():
                try:
                    user['_chat_mentions'][uid] = await recheck_actor(self.db, member) if member is not None else None
                except ChatError:
                    user['_chat_mentions'][uid] = None
            yield user, target

    async def _conversation(self, user: dict, cid: str):
        c = await self.db.chat_get_conversation(user['account_id'], cid, lock=True)
        return c, await conversation_access(self.db, user, c)

    async def _view(self, user: dict, cid: str) -> dict:
        c, access = await self._conversation(user, cid)
        return _result(c, access)

    async def get_conversation(self, actor: Actor, conversation_id: str) -> dict:
        async with self._transaction(actor, conversation_id=conversation_id) as (user, _):
            return await self._view(user, conversation_id)

    async def ensure_general(self, actor: Actor) -> dict:
        async with self._transaction(actor) as (user, _):
            cid = await self.db.chat_find_general(actor.account_id)
            if cid is None:
                owner = await self.db.chat_primary_owner(actor.account_id)
                if owner is None:
                    raise ChatError('owner_unavailable')
                cid = await self.db.chat_insert_conversation(
                    actor.account_id, kind='account', title='General', owner_user_id=owner['id'], system_key='general',
                )
                await self.db.chat_record_group_change(actor.account_id, cid, user['id'], 'chat_group_created', {})
            return await self._view(user, cid)

    async def create_group(self, actor: Actor, *, title: str, kind='account', role_keys=(),
                           description='', posting_mode='everyone', new_member_history='all') -> dict:
        title, description = _text(title, 80, required=True), _text(description, 300)
        _modes(posting_mode, new_member_history)
        if kind not in ('account', 'roles') or (kind == 'account' and role_keys):
            raise ChatError('invalid_audience')
        roles = _roles(role_keys) if kind == 'roles' else []
        async with self._transaction(actor) as (user, _):
            if kind == 'roles' and user['role'] not in roles:
                raise ChatError('invalid_audience')
            if await self.db.chat_active_group_count(actor.account_id) >= MAX_ACTIVE_GROUPS:
                raise ChatError('group_limit')
            cid = await self.db.chat_insert_conversation(
                actor.account_id, kind=kind, owner_user_id=user['id'], title=title, description=description,
                role_keys=roles, posting_mode=posting_mode, new_member_history=new_member_history,
            )
            await self.db.chat_record_group_change(actor.account_id, cid, user['id'], 'chat_group_created', {})
            return await self._view(user, cid)

    async def ensure_direct(self, actor: Actor, other_user_id: int) -> dict:
        if type(other_user_id) is not int or other_user_id == actor.user_id:
            raise ChatError('invalid_participant')
        async with self._transaction(actor, target_id=other_user_id) as (user, _):
            low, high = sorted((actor.user_id, other_user_id))
            cid = await self.db.chat_find_direct(actor.account_id, low, high)
            if cid is None:
                cid = await self.db.chat_insert_conversation(actor.account_id, kind='direct', direct_pair=(low, high))
            user['_chat_peer_granted'] = True  # Both participants were resolved and rechecked.
            # DM creation is private, not an account-visible Activity Trail event.
            return await self._view(user, cid)

    async def update_group(self, actor: Actor, conversation_id: str, *, expected_version: int, changes: dict) -> dict:
        if not isinstance(changes, dict) or not changes or not changes.keys() <= {
            'title', 'description', 'posting_mode', 'new_member_history', 'role_keys', 'archived',
        }:
            raise ChatError('invalid_settings')
        patch = dict(changes)
        for field, limit in (('title', 80), ('description', 300)):
            if field in patch:
                patch[field] = _text(patch[field], limit, required=field == 'title')
        if 'role_keys' in patch:
            patch['role_keys'] = _roles(patch['role_keys'])
        if 'archived' in patch and type(patch['archived']) is not bool:
            raise ChatError('invalid_settings')
        async with self._transaction(actor) as (user, _):
            c, access = await self._conversation(user, conversation_id)
            if c['kind'] == 'direct':
                raise ChatError('not_found')
            _version(c, expected_version)
            for field in patch:
                action = ('manage_info' if field in ('title', 'description') else
                          'manage_audience' if field == 'role_keys' else
                          'archive_group' if field == 'archived' else 'manage_settings')
                require_action(access, action)
            _modes(patch.get('posting_mode', c['posting_mode']), patch.get('new_member_history', c['new_member_history']))
            if 'role_keys' in patch:
                owner = await self.db.chat_get_user(actor.account_id, c['owner_user_id'])
                if c['kind'] != 'roles' or not structurally_eligible(owner, {**c, 'role_keys': patch['role_keys']}):
                    raise ChatError('owner_ineligible')
                # A delegated admin cannot remove their own eligibility and
                # receive an authorized response with stale caller actions.
                if user['role'] not in patch['role_keys']:
                    raise ChatError('invalid_audience')
            if patch.get('archived') is False and c['archived']:
                if await self.db.chat_active_group_count(actor.account_id) >= MAX_ACTIVE_GROUPS:
                    raise ChatError('group_limit')
            roles = patch.pop('role_keys', None)
            await self.db.chat_update_conversation(actor.account_id, conversation_id, patch)
            if roles is not None:
                await self.db.chat_replace_roles(actor.account_id, conversation_id, roles)
            # The trail records changed field names, not private group text.
            await self.db.chat_record_group_change(actor.account_id, conversation_id, user['id'], 'chat_group_updated',
                                                  {field: {'changed': True} for field in changes})
            return await self._view(user, conversation_id)

    async def set_admin(self, actor: Actor, conversation_id: str, user_id: int, *, expected_version: int,
                        actions: list[str] | None) -> dict:
        if actions is not None and (not isinstance(actions, list) or
                any(not isinstance(a, str) or a not in ADMIN_ACTIONS for a in actions)):
            raise ChatError('invalid_admin_actions')
        # Removal works even after the target loses their Chat grant/role.
        async with self._transaction(actor, target_id=user_id if actions is not None else None) as (user, target):
            c, access = await self._conversation(user, conversation_id)
            require_action(access, 'manage_admins')
            _version(c, expected_version)
            if c['owner_user_id'] == user_id:
                raise ChatError('invalid_admin')
            if actions is not None and not structurally_eligible(target, c):
                raise ChatError('not_found')
            await self.db.chat_set_admin(actor.account_id, conversation_id, user_id,
                                         sorted(set(actions)) if actions is not None else None)
            await self.db.chat_update_conversation(actor.account_id, conversation_id, {})
            await self.db.chat_record_group_change(actor.account_id, conversation_id, user['id'], 'chat_admin_changed',
                                                  {'user_id': {'to': user_id}, 'actions': {'to': actions}})
            return await self._view(user, conversation_id)

    async def transfer_ownership(self, actor: Actor, conversation_id: str, new_owner_id: int, *,
                                 expected_version: int, recovery=False) -> dict:
        async with self._transaction(actor, target_id=new_owner_id) as (user, target):
            if recovery:
                if user['role'] != 'owner' or not user['is_primary_owner']:
                    raise ChatError('forbidden')
                primary = await self.db.chat_primary_owner(actor.account_id)
                if primary is None or primary['id'] != user['id']:
                    raise ChatError('forbidden')
                c = await self.db.chat_get_conversation(actor.account_id, conversation_id, lock=True)
                if c is None or c['kind'] == 'direct':
                    raise ChatError('not_found')
                old_owner = await self.db.chat_get_user(actor.account_id, c['owner_user_id'])
                if structurally_eligible(old_owner, c):
                    raise ChatError('owner_still_eligible')
            else:
                c, access = await self._conversation(user, conversation_id)
                require_action(access, 'transfer_ownership')
            _version(c, expected_version)
            if c['owner_user_id'] == new_owner_id or not structurally_eligible(target, c):
                raise ChatError('invalid_owner')
            if await self.db.chat_membership(actor.account_id, conversation_id, new_owner_id) is None:
                raise ChatError('not_found')
            old_id = c['owner_user_id']
            await self.db.chat_update_conversation(actor.account_id, conversation_id, {'owner_user_id': new_owner_id})
            for uid in (old_id, new_owner_id):
                await self.db.chat_set_admin(actor.account_id, conversation_id, uid, None)
            await self.db.chat_record_group_change(actor.account_id, conversation_id, user['id'],
                'chat_ownership_recovered' if recovery else 'chat_ownership_transferred',
                {'owner_user_id': {'from': old_id, 'to': new_owner_id}})
            if recovery:
                # The operator need not belong to this role audience; return
                # only the recovery receipt, never a conversation/message view.
                return {'id': conversation_id, 'owner_user_id': new_owner_id, 'version': c['version'] + 1}
            return await self._view(user, conversation_id)
