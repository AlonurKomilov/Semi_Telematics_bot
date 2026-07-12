"""Chat threading — ai_conversations CRUD, scoping, and the legacy backfill.

The dashboard's History panel: each user has named threads with per-thread
open/export/delete; messages carry ``conversation_id``; pre-threading rows
get folded into one "Earlier conversation" by the migration.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

from capabilities.permissions.roles import Role


async def _seed_user(db, name="Threads Co"):
    from interfaces.api.auth import _hash_password
    acct = await db.create_account(name)
    user = await db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id,
        role=Role.OWNER,
        display_name="Owner",
    )
    return acct.id, (user.telegram_id or user.id)


class TestThreading:
    async def test_first_message_creates_thread_titled_from_question(self, pg_db):
        acct, uid = await _seed_user(pg_db)
        conv_id = await pg_db.save_chat_messages(
            acct, uid, "any overdue maintenance tasks?", "Truck 228 is overdue.",
        )
        convs = await pg_db.list_ai_conversations(acct, uid)
        assert [c["id"] for c in convs] == [conv_id]
        assert convs[0]["title"] == "any overdue maintenance tasks?"
        assert convs[0]["message_count"] == 2

    async def test_no_id_reuses_latest_thread(self, pg_db):
        acct, uid = await _seed_user(pg_db)
        first = await pg_db.save_chat_messages(acct, uid, "q1", "a1")
        second = await pg_db.save_chat_messages(acct, uid, "q2", "a2")
        assert first == second
        convs = await pg_db.list_ai_conversations(acct, uid)
        assert len(convs) == 1 and convs[0]["message_count"] == 4

    async def test_new_thread_isolates_context(self, pg_db):
        acct, uid = await _seed_user(pg_db)
        old = await pg_db.save_chat_messages(acct, uid, "old topic", "old answer")
        fresh = await pg_db.create_ai_conversation(acct, uid, "fresh topic")
        await pg_db.save_chat_messages(acct, uid, "fresh topic", "fresh answer",
                                       conversation_id=fresh)

        old_rows = await pg_db.get_chat_history(acct, uid, conversation_id=old)
        fresh_rows = await pg_db.get_chat_history(acct, uid, conversation_id=fresh)
        assert [r["text"] for r in old_rows] == ["old topic", "old answer"]
        assert [r["text"] for r in fresh_rows] == ["fresh topic", "fresh answer"]

    async def test_stale_or_foreign_id_falls_through_to_new_thread(self, pg_db):
        acct, uid = await _seed_user(pg_db)
        conv = await pg_db.save_chat_messages(
            acct, uid, "q", "a", conversation_id=999_999,  # nonexistent
        )
        assert conv != 999_999
        rows = await pg_db.get_chat_history(acct, uid, conversation_id=conv)
        assert len(rows) == 2

    async def test_delete_is_scoped_to_the_owner(self, pg_db):
        acct, uid = await _seed_user(pg_db)
        conv = await pg_db.save_chat_messages(acct, uid, "mine", "yes")

        # Another user in the same account can't delete it…
        assert await pg_db.delete_ai_conversation(acct, uid + 1, conv) is False
        assert len(await pg_db.list_ai_conversations(acct, uid)) == 1

        # …the owner can, and the messages go with it.
        assert await pg_db.delete_ai_conversation(acct, uid, conv) is True
        assert await pg_db.list_ai_conversations(acct, uid) == []
        assert await pg_db.get_chat_history(acct, uid, conversation_id=conv) == []

    async def test_migration_backfills_legacy_rows_into_one_thread(self, pg_db):
        from adapters.storage.platform_migrations import migrate_ai_conversations
        acct, uid = await _seed_user(pg_db)
        # Simulate pre-threading rows (conversation_id NULL).
        for role, text, ts in (
            ("user", "legacy q", "2026-01-01T10:00:00+00:00"),
            ("model", "legacy a", "2026-01-02T10:00:00+00:00"),
        ):
            await pg_db._db.execute(
                "INSERT INTO ai_chat_history"
                " (account_id, user_id, role, text, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (acct, uid, role, text, ts),
            )
        await pg_db._db.commit()

        await migrate_ai_conversations(pg_db._db)

        convs = await pg_db.list_ai_conversations(acct, uid)
        assert len(convs) == 1
        assert convs[0]["title"] == "Earlier conversation"
        assert convs[0]["created_at"] == "2026-01-01T10:00:00+00:00"
        assert convs[0]["updated_at"] == "2026-01-02T10:00:00+00:00"
        rows = await pg_db.get_chat_history(
            acct, uid, conversation_id=convs[0]["id"],
        )
        assert [r["text"] for r in rows] == ["legacy q", "legacy a"]

    async def test_age_prune_drops_emptied_threads(self, pg_db):
        from datetime import datetime, timedelta, timezone
        acct, uid = await _seed_user(pg_db)
        conv = await pg_db.create_ai_conversation(acct, uid, "ancient thread")
        old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        await pg_db._db.execute(
            "INSERT INTO ai_chat_history"
            " (account_id, user_id, role, text, created_at, conversation_id)"
            " VALUES (?, ?, 'user', 'ancient', ?, ?)",
            (acct, uid, old_ts, conv),
        )
        await pg_db._db.commit()

        deleted = await pg_db.prune_ai_chat_history(days=90)

        assert deleted == 1
        assert await pg_db.list_ai_conversations(acct, uid) == []
