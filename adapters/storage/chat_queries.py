"""Chat discovery projections; no email, phone or driver identifiers."""


class ChatQueriesMixin:
    async def chat_profile(self, aid, uid):
        cur = await self._db.execute('SELECT id, display_name, role, is_active FROM users WHERE account_id = ? AND id = ?', (aid, uid))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def chat_people_candidates(self, aid, *, after=0, query='', limit=101):
        cur = await self._db.execute(
            "SELECT id, display_name, role FROM users WHERE account_id = ? AND is_active = 1 AND id > ? "
            "AND strpos(lower(COALESCE(display_name, '')), lower(?::text)) > 0 ORDER BY id LIMIT ?", (aid, after, query, limit))
        return [dict(r) for r in await cur.fetchall()]

    async def chat_conversation_ids(self, aid, uid, *, after='', limit=51):
        cur = await self._db.execute(
            "SELECT c.id FROM chat_conversations c JOIN chat_membership_intervals m ON m.account_id = c.account_id "
            "AND m.conversation_id = c.id AND m.user_id = ? AND m.left_at IS NULL "
            "JOIN users u ON u.account_id = c.account_id AND u.id = m.user_id AND u.is_active = 1 "
            "WHERE c.account_id = ? AND c.id > ? AND (c.kind = 'account' "
            "OR (c.kind = 'direct' AND u.id IN (c.direct_low_user_id, c.direct_high_user_id)) "
            "OR (c.kind = 'roles' AND EXISTS (SELECT 1 FROM chat_conversation_roles r WHERE r.account_id = c.account_id "
            "AND r.conversation_id = c.id AND r.role_key = u.role))) ORDER BY c.id LIMIT ?", (uid, aid, after, limit))
        return [r['id'] for r in await cur.fetchall()]

    async def chat_summary(self, aid, cid, uid, boundary):
        state = await self.chat_state(aid, cid, uid)
        cur = await self._db.execute(
            'SELECT count(*) AS n, count(*) FILTER (WHERE EXISTS (SELECT 1 FROM chat_mentions x '
            'WHERE x.account_id = m.account_id AND x.conversation_id = m.conversation_id '
            'AND x.message_id = m.id AND x.user_id = ?)) AS unread_mentions, '
            "EXISTS (SELECT 1 FROM chat_drafts d WHERE d.account_id = ? AND d.conversation_id = ? "
            "AND d.user_id = ? AND (d.body <> '' OR d.reply_to_id IS NOT NULL)) AS has_draft "
            'FROM chat_messages m WHERE m.account_id = ? AND m.conversation_id = ? '
            'AND m.message_seq > ? AND m.author_id <> ? AND m.deleted_at IS NULL',
            (uid, aid, cid, uid, aid, cid, max(boundary, state['last_read_message_seq']), uid))
        counts = await cur.fetchone()
        cur = await self._db.execute(
            'SELECT * FROM chat_messages WHERE account_id = ? AND conversation_id = ? '
            'AND message_seq > ? AND deleted_at IS NULL ORDER BY message_seq DESC LIMIT 1', (aid, cid, boundary))
        row = await cur.fetchone()
        return {'state': state, 'unread_count': counts['n'], 'unread_mentions': counts['unread_mentions'],
                'has_draft': counts['has_draft'], 'latest': dict(row) if row else None}

    async def chat_admins(self, aid, cid):
        cur = await self._db.execute(
            'SELECT a.user_id, a.actions, u.display_name, u.role, u.is_active FROM chat_group_admins a '
            'JOIN users u ON u.account_id = a.account_id AND u.id = a.user_id '
            'WHERE a.account_id = ? AND a.conversation_id = ? ORDER BY a.user_id', (aid, cid))
        return [dict(r) for r in await cur.fetchall()]

    async def chat_recovery_candidates(self, aid, *, after='', limit=51):
        cur = await self._db.execute(
            "SELECT c.id, c.owner_user_id, c.version FROM chat_conversations c JOIN users u "
            "ON u.account_id = c.account_id AND u.id = c.owner_user_id WHERE c.account_id = ? AND c.id > ? "
            "AND c.kind <> 'direct' AND (u.is_active <> 1 OR (c.kind = 'roles' AND NOT EXISTS "
            "(SELECT 1 FROM chat_conversation_roles r WHERE r.account_id = c.account_id "
            "AND r.conversation_id = c.id AND r.role_key = u.role))) ORDER BY c.id LIMIT ?", (aid, after, limit))
        return [dict(r) for r in await cur.fetchall()]
