"""Durable message operations sharing the foundation's access/transaction seam."""
import hashlib
import json

from .access import require_action, structurally_eligible
from .models import ChatError
from .validation import mention_ids, page, positive, text, version


class MessageOperations:
    async def _visible_message(self, actor, cid, mid, access, *, live=False):
        positive(mid)
        message = await self.db.chat_message(actor.account_id, cid, mid)
        if message is None or not access.sees_message(message['message_seq']):
            raise ChatError('not_found')
        if live and message['deleted_at'] is not None:
            raise ChatError('not_found')
        return message

    async def _quote(self, actor, cid, mid, access):
        if mid is None:
            return None
        quoted = await self.db.chat_message(actor.account_id, cid, mid)
        if quoted is None or quoted['deleted_at'] is not None or not access.sees_message(quoted['message_seq']):
            return {'status': 'unavailable'}
        return {'status': 'available', 'id': quoted['id'], 'author_id': quoted['author_id'], 'body': quoted['body']}

    async def _message_result(self, actor, c, access, message):
        author = await self.db.chat_profile(actor.account_id, message['author_id'])
        deleted = message['deleted_at'] is not None
        result = {
            'id': message['id'], 'message_seq': message['message_seq'], 'version': message['version'],
            'author': {'id': message['author_id'], 'display_name': (author or {}).get('display_name') or 'Former member'},
            'body': None if deleted else message['body'], 'created_at': message['created_at'],
            'edited_at': message['edited_at'], 'deleted_at': message['deleted_at'],
            'mentions': [] if deleted else await self.db.chat_message_mentions(actor.account_id, c['id'], message['id']),
            'reply': None if deleted else await self._quote(actor, c['id'], message['reply_to_id'], access),
            'can_edit': not deleted and message['author_id'] == actor.user_id and 'send' in access.actions,
            'can_delete': not deleted and ('delete_messages' in access.actions or
                                          (message['author_id'] == actor.user_id and 'send' in access.actions)),
        }
        if message['author_id'] == actor.user_id:
            result['client_message_id'] = message['client_message_id']
        return result

    async def _validate_mentions(self, actor, user, c, mentions, *, message_seq=None):
        for uid in mentions:
            target = user['_chat_mentions'].get(uid)
            if not structurally_eligible(target, c):
                raise ChatError('invalid_mention')
            membership = await self.db.chat_membership(actor.account_id, c['id'], uid)
            visible_seq = c['message_seq'] + 1 if message_seq is None else message_seq
            if membership is None or visible_seq <= membership['visible_after_message_seq']:
                raise ChatError('invalid_mention')

    async def _send_result(self, actor, c, access, message, *, replayed, draft_version, body, reply_to_id):
        result = {'message': await self._message_result(actor, c, access, message), 'replayed': replayed}
        if draft_version is not None:
            draft = await self.db.chat_draft(actor.account_id, c['id'], actor.user_id)
            # Clear exactly the draft this send consumed, in the message transaction.
            # Losing the HTTP response then reloading cannot restore already-sent text.
            # A newer draft from another device is never cleared by an old retry.
            if ('send' in access.actions and draft['version'] == draft_version
                    and draft['body'].strip() == body and draft['reply_to_id'] == reply_to_id):
                draft = await self.db.chat_save_draft(actor.account_id, c['id'], actor.user_id, '', None)
            result['draft'] = {'body': draft['body'], 'version': draft['version'],
                               'reply': await self._quote(actor, c['id'], draft['reply_to_id'], access)}
        return result

    async def send_message(self, actor, cid, *, client_message_id, body, reply_to_id=None, mentions=(), draft_version=None):
        body = text(body, 4000, required=True)
        if draft_version is not None:
            positive(draft_version, zero=True)
        client_id = text(client_message_id, 128, required=True)
        mentions = mention_ids(mentions)
        if reply_to_id is not None:
            positive(reply_to_id)
        payload = json.dumps([body, reply_to_id, mentions], ensure_ascii=False, separators=(',', ':'))
        fingerprint = hashlib.sha256(payload.encode('utf-8')).hexdigest()
        async with self._transaction(actor, conversation_id=cid, mentioned_ids=mentions) as (user, _):
            c, access = await self._conversation(user, cid)
            existing = await self.db.chat_message_by_client_id(actor.account_id, cid, actor.user_id, client_id)
            if existing is not None:
                # The original may now be edited/deleted or outside a rejoin's
                # boundary. Never return its content before current visibility.
                if not access.sees_message(existing['message_seq']):
                    raise ChatError('not_found')
                if existing['request_fingerprint'] != fingerprint:
                    raise ChatError('idempotency_conflict')
                return await self._send_result(actor, c, access, existing, replayed=True, draft_version=draft_version,
                                               body=body, reply_to_id=reply_to_id)
            require_action(access, 'send')
            if reply_to_id is not None:
                await self._visible_message(actor, cid, reply_to_id, access, live=True)
            await self._validate_mentions(actor, user, c, mentions)
            message = await self.db.chat_insert_message(actor.account_id, cid, actor.user_id, client_id,
                                                        fingerprint, body, reply_to_id, mentions)
            await self.db.chat_observe(actor.account_id, cid, actor.user_id, message['message_seq'])
            return await self._send_result(actor, c, access, message, replayed=False, draft_version=draft_version,
                                           body=body, reply_to_id=reply_to_id)

    async def get_message(self, actor, cid, mid):
        async with self._transaction(actor, conversation_id=cid) as (user, _):
            c, access = await self._conversation(user, cid)
            message = await self._visible_message(actor, cid, mid, access)
            await self.db.chat_observe(actor.account_id, cid, actor.user_id, message['message_seq'])
            return await self._message_result(actor, c, access, message)

    async def list_messages(self, actor, cid, *, before=None, limit=50, query=None, pinned=False):
        page(limit, before)
        if query is not None:
            query = text(query, 200, required=True)
        async with self._transaction(actor, conversation_id=cid) as (user, _):
            c, access = await self._conversation(user, cid)
            rows = await self.db.chat_history(actor.account_id, cid, access.visible_after_message_seq,
                                              before=before, limit=limit + 1, query=query, pinned=pinned)
            more, rows = len(rows) > limit, rows[:limit]
            if rows:
                await self.db.chat_observe(actor.account_id, cid, actor.user_id, rows[0]['message_seq'])
            return {'items': [await self._message_result(actor, c, access, row) for row in rows],
                    'next_before': rows[-1]['message_seq'] if more else None}

    async def edit_message(self, actor, cid, mid, *, expected_version, body, mentions=()):
        body, mentions = text(body, 4000, required=True), mention_ids(mentions)
        async with self._transaction(actor, conversation_id=cid, mentioned_ids=mentions) as (user, _):
            c, access = await self._conversation(user, cid)
            message = await self._visible_message(actor, cid, mid, access, live=True)
            if message['author_id'] != actor.user_id:
                raise ChatError('forbidden')
            require_action(access, 'send')
            version(message, expected_version)
            await self._validate_mentions(actor, user, c, mentions, message_seq=message['message_seq'])
            message = await self.db.chat_edit_message(actor.account_id, cid, mid, body, mentions)
            return await self._message_result(actor, c, access, message)

    async def delete_message(self, actor, cid, mid, *, expected_version):
        async with self._transaction(actor, conversation_id=cid) as (user, _):
            c, access = await self._conversation(user, cid)
            message = await self._visible_message(actor, cid, mid, access)
            if not ('delete_messages' in access.actions or (message['author_id'] == actor.user_id and 'send' in access.actions)):
                raise ChatError('forbidden')
            version(message, expected_version)
            if message['deleted_at'] is None:
                message = await self.db.chat_delete_message(actor.account_id, cid, mid)
            return await self._message_result(actor, c, access, message)

    async def pin_message(self, actor, cid, mid, *, expected_version, pinned=True):
        if type(pinned) is not bool:
            raise ChatError('invalid_settings')
        async with self._transaction(actor, conversation_id=cid) as (user, _):
            c, access = await self._conversation(user, cid)
            require_action(access, 'pin_messages')
            message = await self._visible_message(actor, cid, mid, access, live=True)
            version(message, expected_version)
            changed = await self.db.chat_set_pin(actor.account_id, cid, mid, actor.user_id, pinned)
            if changed:
                await self.db.chat_record_message_event(actor.account_id, cid, mid,
                    'message_pinned' if pinned else 'message_unpinned', message['version'], message['message_seq'])
            return {'message_id': mid, 'pinned': pinned}

    async def read_messages(self, actor, cid, *, through_message_seq):
        positive(through_message_seq, zero=True)
        async with self._transaction(actor) as (user, _):
            c, access = await self._conversation(user, cid)
            state = await self.db.chat_state(actor.account_id, cid, actor.user_id)
            if through_message_seq > min(c['message_seq'], state['last_seen_message_seq']):
                raise ChatError('unseen_cursor')
            if through_message_seq and not access.sees_message(through_message_seq):
                raise ChatError('invalid_cursor')
            await self.db.chat_set_read(actor.account_id, cid, actor.user_id, through_message_seq)
            return await self.db.chat_state(actor.account_id, cid, actor.user_id)

    async def personal_state(self, actor, cid, *, changes=None):
        if changes is not None and (not isinstance(changes, dict) or not changes or
                not changes.keys() <= {'muted', 'pinned', 'archived'} or any(type(v) is not bool for v in changes.values())):
            raise ChatError('invalid_settings')
        async with self._transaction(actor) as (user, _):
            await self._conversation(user, cid)
            if changes is not None:
                await self.db.chat_set_personal_state(actor.account_id, cid, actor.user_id, changes)
            return await self.db.chat_state(actor.account_id, cid, actor.user_id)

    async def draft(self, actor, cid, *, body=None, reply_to_id=None, expected_version=None):
        if body is not None:
            body = text(body, 4000, trim=False)
        if reply_to_id is not None:
            positive(reply_to_id)
        async with self._transaction(actor, conversation_id=cid) as (user, _):
            c, access = await self._conversation(user, cid)
            draft = await self.db.chat_draft(actor.account_id, cid, actor.user_id)
            if body is not None:
                require_action(access, 'send')
                version(draft, expected_version)
                if reply_to_id is not None:
                    await self._visible_message(actor, cid, reply_to_id, access, live=True)
                draft = await self.db.chat_save_draft(actor.account_id, cid, actor.user_id, body, reply_to_id)
            quote = await self._quote(actor, cid, draft['reply_to_id'], access)
            return {'body': draft['body'], 'version': draft['version'], 'reply': quote}
