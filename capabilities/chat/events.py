"""Visibility-filtered journal replay used by HTTP and live connections."""
from .models import ChatError
from .access import resolve_actor
from .validation import page


class ChatEvents:
    async def sync_revision(self, actor):
        # An opaque caller-only invalidation counter needs no conversation or
        # account write lock. Resource reads still use the locked access path.
        await resolve_actor(self.db, actor)
        return await self.db.chat_sync_revision(actor.account_id, actor.user_id)

    async def replay(self, actor, cid, *, after=None, limit=50):
        page(limit)
        if after is not None and (type(after) is not int or after < 0 or after > 2**63 - 1):
            raise ChatError('invalid_cursor')
        async with self._transaction(actor, conversation_id=cid) as (user, _):
            c, access = await self._conversation(user, cid)
            member = await self.db.chat_membership(actor.account_id, cid, actor.user_id)
            result = {'conversation_id': cid, 'items': [], 'cursor': c['event_seq'], 'has_more': False,
                      'resync_required': after is None,
                      'caller': {'group_role': access.group_role, 'actions': sorted(access.actions)},
                      'scope': {'membership_id': member['id'], 'visible_after_message_seq': access.visible_after_message_seq}}
            if after is None:
                return result  # checkpoint BEFORE snapshot reads, then replay from here
            rows = await self.db.chat_events_after(actor.account_id, cid, after, limit)
            # Check the SCANNED journal, not the filtered result: private and
            # hidden messages legitimately leave gaps in the delivered stream.
            if (after > c['event_seq'] or
                    (after < c['event_seq'] and not rows) or
                    any(r['event_seq'] != after + i + 1 for i, r in enumerate(rows))):
                result['resync_required'] = True
                return result
            scanned = rows[-1]['event_seq'] if rows else after
            result.update(cursor=scanned, has_more=scanned < c['event_seq'])
            observed = 0
            for event in rows:
                if event['recipient_user_id'] not in (None, actor.user_id):
                    continue
                item = {'id': event['id'], 'event_seq': event['event_seq'], 'kind': event['kind'],
                        'resource_version': event['resource_version']}
                if event['message_id'] is not None:
                    message = await self.db.chat_message(actor.account_id, cid, event['message_id'])
                    if not message or not access.sees_message(message['message_seq']):
                        continue
                    # Always render the CURRENT version, including tombstones.
                    item['message'] = await self._message_result(actor, c, access, message)
                    observed = max(observed, message['message_seq'])
                # Metadata/private events invalidate their HTTP resource. No
                # old title, draft body, ACL or other user's personal state.
                result['items'].append(item)
            if observed:
                await self.db.chat_observe(actor.account_id, cid, actor.user_id, observed)
            return result
