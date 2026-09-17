"""Durable Chat message/state queries. All writes share the Chat account lock.

Only the domain layer calls these methods, with current actor access and a
history boundary. Query filtering precedes LIMIT. Events/outbox hold refs,
never message text; P3 delivery must reauthorize each reference.
"""


class ChatMessagesMixin:
    async def chat_message(self, aid, cid, mid):
        cur = await self._db.execute(
            'SELECT * FROM chat_messages WHERE account_id = ? AND conversation_id = ? AND id = ?', (aid, cid, mid))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def chat_message_by_client_id(self, aid, cid, uid, client_id):
        cur = await self._db.execute(
            'SELECT * FROM chat_messages WHERE account_id = ? AND conversation_id = ? AND author_id = ? AND client_message_id = ?',
            (aid, cid, uid, client_id))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def chat_message_mentions(self, aid, cid, mid):
        cur = await self._db.execute(
            'SELECT user_id FROM chat_mentions WHERE account_id = ? AND conversation_id = ? AND message_id = ? ORDER BY user_id',
            (aid, cid, mid))
        return [r['user_id'] for r in await cur.fetchall()]

    async def chat_replace_mentions(self, aid, cid, mid, user_ids):
        await self._db.execute('DELETE FROM chat_mentions WHERE account_id = ? AND conversation_id = ? AND message_id = ?',
                               (aid, cid, mid))
        for uid in user_ids:
            await self._db.execute(
                'INSERT INTO chat_mentions (account_id, conversation_id, message_id, user_id) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING',
                (aid, cid, mid, uid))

    async def chat_insert_message(self, aid, cid, uid, client_id, fingerprint, body, reply_id, mentions):
        cur = await self._db.execute(
            'UPDATE chat_conversations SET message_seq = message_seq + 1, updated_at = clock_timestamp() '
            'WHERE account_id = ? AND id = ? RETURNING message_seq', (aid, cid))
        seq = (await cur.fetchone())['message_seq']
        cur = await self._db.execute(
            'INSERT INTO chat_messages (account_id, conversation_id, author_id, client_message_id, request_fingerprint, message_seq, body, reply_to_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *', (aid, cid, uid, client_id, fingerprint, seq, body, reply_id))
        row = dict(await cur.fetchone())
        await self.chat_replace_mentions(aid, cid, row['id'], mentions)
        await self.chat_record_message_event(aid, cid, row['id'], 'message_created', row['version'], seq)
        return row

    async def chat_edit_message(self, aid, cid, mid, body, mentions):
        cur = await self._db.execute(
            'UPDATE chat_messages SET body = ?, version = version + 1, edited_at = clock_timestamp() '
            'WHERE account_id = ? AND conversation_id = ? AND id = ? RETURNING *', (body, aid, cid, mid))
        row = dict(await cur.fetchone())
        await self.chat_replace_mentions(aid, cid, mid, mentions)
        await self.chat_record_message_event(aid, cid, mid, 'message_edited', row['version'], row['message_seq'])
        return row

    async def chat_delete_message(self, aid, cid, mid):
        cur = await self._db.execute(
            'UPDATE chat_messages SET body = NULL, reply_to_id = NULL, deleted_at = clock_timestamp(), version = version + 1 '
            'WHERE account_id = ? AND conversation_id = ? AND id = ? RETURNING *', (aid, cid, mid))
        row = dict(await cur.fetchone())
        await self.chat_replace_mentions(aid, cid, mid, [])
        await self._db.execute('DELETE FROM chat_pins WHERE account_id = ? AND conversation_id = ? AND message_id = ?', (aid, cid, mid))
        await self.chat_record_message_event(aid, cid, mid, 'message_deleted', row['version'], row['message_seq'])
        return row

    async def chat_record_message_event(self, aid, cid, mid, kind, version, message_seq):
        cur = await self._db.execute(
            'UPDATE chat_conversations SET event_seq = event_seq + 1 WHERE account_id = ? AND id = ? RETURNING event_seq', (aid, cid))
        seq = (await cur.fetchone())['event_seq']
        cur = await self._db.execute(
            'INSERT INTO chat_events (account_id, conversation_id, event_seq, kind, message_id, resource_version) '
            'VALUES (?, ?, ?, ?, ?, ?) RETURNING id', (aid, cid, seq, kind, mid, version))
        await cur.fetchone()  # Fanout is atomic in chat_event_fanout (migration 214).
        return seq

    async def chat_history(self, aid, cid, boundary, *, before=None, limit=51, query=None, pinned=False):
        clauses = ['m.account_id = ?', 'm.conversation_id = ?', 'm.message_seq > ?']
        params = [aid, cid, boundary]
        if before is not None:
            clauses.append('m.message_seq < ?')
            params.append(before)
        if query is not None:
            clauses.extend(['m.deleted_at IS NULL', 'strpos(lower(m.body), lower(?::text)) > 0'])
            params.append(query)
        if pinned:
            clauses.extend(['m.deleted_at IS NULL', 'EXISTS (SELECT 1 FROM chat_pins p WHERE p.account_id = m.account_id '
                            'AND p.conversation_id = m.conversation_id AND p.message_id = m.id)'])
        params.append(limit)
        cur = await self._db.execute('SELECT m.* FROM chat_messages m WHERE ' + ' AND '.join(clauses) +
                                      ' ORDER BY m.message_seq DESC LIMIT ?', tuple(params))
        return [dict(r) for r in await cur.fetchall()]

    async def chat_set_pin(self, aid, cid, mid, uid, pinned):
        if pinned:
            cur = await self._db.execute(
                'INSERT INTO chat_pins (account_id, conversation_id, message_id, pinned_by) VALUES (?, ?, ?, ?) '
                'ON CONFLICT DO NOTHING RETURNING message_id', (aid, cid, mid, uid))
        else:
            cur = await self._db.execute(
                'DELETE FROM chat_pins WHERE account_id = ? AND conversation_id = ? AND message_id = ? RETURNING message_id',
                (aid, cid, mid))
        return await cur.fetchone() is not None

    async def chat_state(self, aid, cid, uid):
        cur = await self._db.execute('SELECT last_read_message_seq, last_seen_message_seq, muted, pinned, archived '
                                     'FROM chat_member_state WHERE account_id = ? AND conversation_id = ? AND user_id = ?', (aid, cid, uid))
        row = await cur.fetchone()
        return dict(row) if row else {'last_read_message_seq': 0, 'last_seen_message_seq': 0,
                                     'muted': False, 'pinned': False, 'archived': False}

    async def chat_observe(self, aid, cid, uid, seq):
        await self._db.execute(
            'INSERT INTO chat_member_state (account_id, conversation_id, user_id, last_seen_message_seq) VALUES (?, ?, ?, ?) '
            'ON CONFLICT (account_id, conversation_id, user_id) DO UPDATE SET '
            'last_seen_message_seq = GREATEST(chat_member_state.last_seen_message_seq, EXCLUDED.last_seen_message_seq)',
            (aid, cid, uid, seq))

    async def chat_set_read(self, aid, cid, uid, seq):
        await self._db.execute(
            'UPDATE chat_member_state SET last_read_message_seq = GREATEST(last_read_message_seq, ?) '
            'WHERE account_id = ? AND conversation_id = ? AND user_id = ?', (seq, aid, cid, uid))

    async def chat_set_personal_state(self, aid, cid, uid, changes):
        if not changes or not changes.keys() <= {'muted', 'pinned', 'archived'}:
            raise ValueError('Unsupported personal state field')
        await self.chat_observe(aid, cid, uid, 0)
        assignments = ', '.join(f'{key} = ?' for key in changes)
        await self._db.execute('UPDATE chat_member_state SET ' + assignments + ' WHERE account_id = ? AND conversation_id = ? AND user_id = ?',
                               (*changes.values(), aid, cid, uid))

    async def chat_draft(self, aid, cid, uid):
        cur = await self._db.execute('SELECT body, reply_to_id, version FROM chat_drafts '
                                     'WHERE account_id = ? AND conversation_id = ? AND user_id = ?', (aid, cid, uid))
        row = await cur.fetchone()
        return dict(row) if row else {'body': '', 'reply_to_id': None, 'version': 0}

    async def chat_save_draft(self, aid, cid, uid, body, reply_id):
        cur = await self._db.execute(
            'INSERT INTO chat_drafts (account_id, conversation_id, user_id, body, reply_to_id) VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT (account_id, conversation_id, user_id) DO UPDATE SET body = EXCLUDED.body, '
            'reply_to_id = EXCLUDED.reply_to_id, version = chat_drafts.version + 1, updated_at = clock_timestamp() '
            'RETURNING body, reply_to_id, version', (aid, cid, uid, body, reply_id))
        return dict(await cur.fetchone())
