"""Account-scoped Chat persistence; domain authorization lives in capabilities/chat.

Mutations are internal and require the caller's Database.transaction plus
chat_lock_account, then chat_get_conversation(lock=True). Reads use _db too,
so decisions and writes share the transaction's pinned connection.
"""
from uuid import uuid4


class ChatMixin:
    async def chat_lock_account(self, account_id: int) -> None:
        await self._db.execute("SELECT chat_lock_account(?::bigint)", (account_id,))

    async def chat_get_user(self, account_id: int, user_id: int) -> dict | None:
        cur = await self._db.execute(
            "SELECT u.id, u.account_id, u.role, u.is_active, u.is_manager, "
            "u.is_primary_owner, a.is_active AS account_active FROM users u "
            "JOIN accounts a ON a.id = u.account_id WHERE u.account_id = ? AND u.id = ?",
            (account_id, user_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def chat_primary_owner(self, account_id: int) -> dict | None:
        cur = await self._db.execute(
            "SELECT id FROM users WHERE account_id = ? AND role = 'owner' "
            "AND is_primary_owner = 1 AND is_active = 1 ORDER BY id LIMIT 2", (account_id,),
        )
        rows = await cur.fetchall()
        # Ambiguous ownership must be repaired by Team Management, not guessed.
        return await self.chat_get_user(account_id, rows[0]['id']) if len(rows) == 1 else None

    async def chat_get_conversation(self, account_id: int, conversation_id: str, *, lock=False) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM chat_conversations WHERE account_id = ? AND id = ?" +
            (" FOR UPDATE" if lock else ""), (account_id, conversation_id),
        )
        row = await cur.fetchone()
        if not row:
            return None
        result = dict(row)
        if result['kind'] != 'roles':
            result['role_keys'] = []
            return result
        cur = await self._db.execute(
            "SELECT role_key FROM chat_conversation_roles WHERE account_id = ? AND conversation_id = ? ORDER BY role_key",
            (account_id, conversation_id),
        )
        result['role_keys'] = [r['role_key'] for r in await cur.fetchall()]
        return result

    async def chat_find_general(self, account_id: int) -> str | None:
        cur = await self._db.execute(
            "SELECT id FROM chat_conversations WHERE account_id = ? AND system_key = 'general'", (account_id,),
        )
        row = await cur.fetchone()
        return row['id'] if row else None

    async def chat_find_direct(self, account_id: int, low: int, high: int) -> str | None:
        cur = await self._db.execute(
            "SELECT id FROM chat_conversations WHERE account_id = ? AND direct_low_user_id = ? AND direct_high_user_id = ?",
            (account_id, low, high),
        )
        row = await cur.fetchone()
        return row['id'] if row else None

    async def chat_active_group_count(self, account_id: int) -> int:
        cur = await self._db.execute(
            "SELECT count(*) AS n FROM chat_conversations WHERE account_id = ? "
            "AND kind <> 'direct' AND system_key IS NULL AND NOT archived", (account_id,),
        )
        return (await cur.fetchone())['n']

    async def chat_insert_conversation(self, account_id: int, *, kind: str, owner_user_id=None,
                                       title='', description='', role_keys=(), posting_mode='everyone',
                                       new_member_history='all', system_key=None, direct_pair=None) -> str:
        cid = str(uuid4())
        low, high = direct_pair or (None, None)
        await self._db.execute(
            "INSERT INTO chat_conversations (id, account_id, kind, owner_user_id, title, description, "
            "posting_mode, new_member_history, system_key, direct_low_user_id, direct_high_user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (cid, account_id, kind, owner_user_id, title, description, posting_mode,
             new_member_history, system_key, low, high),
        )
        await self.chat_replace_roles(account_id, cid, role_keys)
        return cid

    async def chat_replace_roles(self, account_id: int, conversation_id: str, role_keys) -> None:
        # Keep retained roles in place: delete/reinsert would reset their history.
        cur = await self._db.execute(
            "SELECT role_key FROM chat_conversation_roles WHERE account_id = ? AND conversation_id = ?",
            (account_id, conversation_id),
        )
        before = {r['role_key'] for r in await cur.fetchall()}
        after = set(role_keys)
        for role in sorted(before - after):
            await self._db.execute(
                "DELETE FROM chat_conversation_roles WHERE account_id = ? AND conversation_id = ? AND role_key = ?",
                (account_id, conversation_id, role),
            )
        for role in sorted(after - before):
            await self._db.execute(
                "INSERT INTO chat_conversation_roles (account_id, conversation_id, role_key) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                (account_id, conversation_id, role),
            )

    async def chat_membership(self, account_id: int, conversation_id: str, user_id: int) -> dict | None:
        cur = await self._db.execute(
            "SELECT id, visible_after_message_seq FROM chat_membership_intervals "
            "WHERE account_id = ? AND conversation_id = ? AND user_id = ? AND left_at IS NULL",
            (account_id, conversation_id, user_id),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def chat_admin_actions(self, account_id: int, conversation_id: str, user_id: int) -> list | None:
        cur = await self._db.execute(
            "SELECT actions FROM chat_group_admins WHERE account_id = ? AND conversation_id = ? AND user_id = ?",
            (account_id, conversation_id, user_id),
        )
        row = await cur.fetchone()
        return list(row['actions']) if row else None

    async def chat_set_admin(self, account_id: int, conversation_id: str, user_id: int, actions: list | None) -> None:
        if actions is None:
            await self._db.execute(
                "DELETE FROM chat_group_admins WHERE account_id = ? AND conversation_id = ? AND user_id = ?",
                (account_id, conversation_id, user_id),
            )
        else:
            await self._db.execute(
                "INSERT INTO chat_group_admins (account_id, conversation_id, user_id, actions) VALUES (?, ?, ?, ?::text[]) "
                "ON CONFLICT (account_id, conversation_id, user_id) DO UPDATE SET actions = EXCLUDED.actions",
                (account_id, conversation_id, user_id, actions),
            )

    async def chat_update_conversation(self, account_id: int, conversation_id: str, changes: dict) -> None:
        allowed = {'title', 'description', 'posting_mode', 'new_member_history', 'archived', 'owner_user_id'}
        if not changes.keys() <= allowed:
            raise ValueError('Unsupported conversation field')
        assignments = [f'{key} = ?' for key in changes]
        assignments.extend(['version = version + 1', 'updated_at = clock_timestamp()'])
        await self._db.execute(
            'UPDATE chat_conversations SET ' + ', '.join(assignments) + ' WHERE account_id = ? AND id = ?',
            (*changes.values(), account_id, conversation_id),
        )

    async def chat_record_group_change(self, account_id: int, conversation_id: str, actor_id: int,
                                      action: str, changes: dict) -> None:
        await self.append_activity_events(account_id, [{
            'entity_type': 'chat_group', 'entity_id': conversation_id, 'action': action,
            'actor_user_id': actor_id, 'changes': changes,
        }])
        # Reference-only sync journal. Never copy group text or message content.
        cur = await self._db.execute(
            "UPDATE chat_conversations SET event_seq = event_seq + 1 WHERE account_id = ? AND id = ? RETURNING event_seq, version",
            (account_id, conversation_id),
        )
        row = await cur.fetchone()
        await self._db.execute(
            "INSERT INTO chat_events (account_id, conversation_id, event_seq, kind, resource_version) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (account_id, conversation_id, row['event_seq'], action, row['version']),
        )
