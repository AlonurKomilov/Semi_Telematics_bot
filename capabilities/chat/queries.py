"""Authorized discovery and management views; all cursors are bounded scans."""
from .access import require_action, resolve_actor, structurally_eligible
from .models import ChatError
from .validation import page, positive, text


class ChatQueries:
    async def bootstrap(self, actor, *, after='', limit=50):
        page(limit)
        after = text(after, 128)
        await self.ensure_general(actor)
        await resolve_actor(self.db, actor)
        candidates = await self.db.chat_conversation_ids(actor.account_id, actor.user_id, after=after, limit=limit + 1)
        peers = {}
        for cid in candidates[:limit]:
            c = await self.db.chat_get_conversation(actor.account_id, cid)
            if c is not None and c['kind'] == 'direct':
                peers[cid] = c['direct_high_user_id'] if c['direct_low_user_id'] == actor.user_id else c['direct_low_user_id']
        items = []
        async with self._transaction(actor, mentioned_ids=set(peers.values())) as (user, _):
            for cid in candidates[:limit]:
                user['_chat_peer_granted'] = user['_chat_mentions'].get(peers.get(cid)) is not None
                try:
                    c, access = await self._conversation(user, cid)
                except ChatError as exc:
                    if exc.code == 'not_found':
                        continue
                    raise
                view = await self._view(user, cid)
                summary = await self.db.chat_summary(actor.account_id, cid, actor.user_id, access.visible_after_message_seq)
                latest = summary.pop('latest')
                view.update(summary)
                view['latest_message'] = await self._message_result(actor, c, access, latest) if latest else None
                if latest:
                    await self.db.chat_observe(actor.account_id, cid, actor.user_id, latest['message_seq'])
                    view['state']['last_seen_message_seq'] = max(view['state']['last_seen_message_seq'], latest['message_seq'])
                if cid in peers:
                    peer = await self.db.chat_profile(actor.account_id, peers[cid])
                    view['peer'] = {'id': peers[cid], 'display_name': (peer or {}).get('display_name') or 'Former member',
                                    'is_active': bool(peer and peer['is_active'])}
                items.append(view)
        return {'items': items, 'next_after': candidates[limit - 1] if len(candidates) > limit else None}

    async def people(self, actor, *, after=0, limit=50, query='', conversation_id=None, message_id=None, group_roles=False):
        page(limit)
        positive(after, zero=True)
        query = text(query, 200)
        if message_id is not None:
            positive(message_id)
            if conversation_id is None:
                raise ChatError('invalid_settings')
        await resolve_actor(self.db, actor)
        candidates = await self.db.chat_people_candidates(actor.account_id, after=after, query=query, limit=101)
        items, scanned = [], 0
        async with self._transaction(actor, mentioned_ids=[r['id'] for r in candidates[:100]]) as (user, _):
            c, target_seq, admin_ids = None, None, set()
            if conversation_id is not None:
                c, access = await self._conversation(user, conversation_id)
                if group_roles:
                    admin_ids = {r['user_id'] for r in await self.db.chat_admins(actor.account_id, conversation_id)}
                if message_id is not None:
                    message = await self._visible_message(actor, conversation_id, message_id, access, live=True)
                    target_seq = message['message_seq']
            for row in candidates[:100]:
                scanned += 1
                member = user['_chat_mentions'].get(row['id'])
                if member is None:
                    continue
                if c is not None:
                    if not structurally_eligible(member, c):
                        continue
                    interval = await self.db.chat_membership(actor.account_id, c['id'], row['id'])
                    if interval is None or (target_seq is not None and target_seq <= interval['visible_after_message_seq']):
                        continue
                profile = await self.db.chat_profile(actor.account_id, row['id'])
                items.append({'id': row['id'], 'display_name': profile['display_name'] or f"Member {row['id']}", 'role': member['role']})
                if group_roles and c is not None:
                    items[-1]['group_role'] = ('owner' if c['owner_user_id'] == row['id'] else
                                               'admin' if row['id'] in admin_ids else 'member')
                if len(items) == limit:
                    break
        return {'items': items, 'next_after': candidates[scanned - 1]['id'] if len(candidates) > scanned else None}

    async def list_admins(self, actor, cid):
        async with self._transaction(actor) as (user, _):
            c, access = await self._conversation(user, cid)
            require_action(access, 'manage_admins')
            rows = await self.db.chat_admins(actor.account_id, cid)
            return {'owner_user_id': c['owner_user_id'], 'version': c['version'], 'items': rows}

    async def recovery_groups(self, actor, *, after='', limit=50):
        page(limit)
        after = text(after, 128)
        async with self._transaction(actor) as (user, _):
            primary = await self.db.chat_primary_owner(actor.account_id)
            if not primary or primary['id'] != user['id']:
                raise ChatError('forbidden')
            rows = await self.db.chat_recovery_candidates(actor.account_id, after=after, limit=limit + 1)
            return {'items': rows[:limit], 'next_after': rows[limit - 1]['id'] if len(rows) > limit else None}
