"""AI chat-history privacy hardening.

Three layers, one table (``ai_chat_history``):
  1. Text encrypted at rest (``enc::`` Fernet, legacy plaintext readable).
  2. 90-day age-cap via the retention hub (``ai.chat_history`` target).
  3. Hard-delete of a user's chat when they're removed from the team.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENCRYPTION_KEY", "")

import pytest

from capabilities.permissions.roles import Role


async def _seed_owner(db, name="Chat Privacy Co"):
    from interfaces.api.auth import _hash_password
    acct = await db.create_account(name)
    owner = await db.create_user_with_email(
        email=f"owner.{acct.id}@example.com",
        password_hash=_hash_password("ownerpass123"),
        account_id=acct.id,
        role=Role.OWNER,
        display_name="Owner",
    )
    return acct, owner


@pytest.fixture()
def _encryption_on(monkeypatch):
    """Enable real Fernet encryption for the test, restore after."""
    from infra import crypto
    monkeypatch.setenv("ENCRYPTION_KEY", "test-passphrase-for-chat")
    crypto.init_encryption()
    yield
    monkeypatch.setenv("ENCRYPTION_KEY", "")
    crypto.init_encryption()


class TestEncryptionAtRest:
    async def test_text_stored_encrypted_and_read_back_plaintext(
        self, pg_db, _encryption_on,
    ):
        acct, owner = await _seed_owner(pg_db)
        uid = owner.telegram_id or owner.id

        await pg_db.save_chat_messages(
            acct.id, uid, "any overdue maintenance?", "Truck 228 is overdue.",
        )

        # Raw column value is ciphertext…
        cur = await pg_db._db.execute(
            "SELECT text FROM ai_chat_history WHERE account_id = ?", (acct.id,),
        )
        raw = [r[0] for r in await cur.fetchall()]
        assert len(raw) == 2
        assert all(v.startswith("enc::") for v in raw)
        assert not any("overdue" in v for v in raw)

        # …while the reader API returns plaintext.
        rows = await pg_db.get_chat_history(acct.id, uid)
        assert [r["text"] for r in rows] == [
            "any overdue maintenance?", "Truck 228 is overdue.",
        ]

    async def test_tier_persists_but_thought_logs_never_touch_the_db(
        self, pg_db, _encryption_on,
    ):
        """Policy: the chain-of-thought and process timeline are
        browser-local (localStorage) — the DB carries only the message
        text (for the model's follow-up context) and the tier label.
        The migration DROPS the short-lived reasoning/process columns.
        """
        acct_obj, owner = await _seed_owner(pg_db, "Local Thoughts Co")
        acct = acct_obj.id
        uid = owner.telegram_id or owner.id

        await pg_db.save_chat_messages(
            acct, uid, "any overdue?", "Truck 228 is overdue.",
            model_tier="Reasoning",
        )

        # The thought-log columns don't exist at all.
        cur = await pg_db._db.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_name = 'ai_chat_history'""",
        )
        cols = {r[0] for r in await cur.fetchall()}
        assert "reasoning" not in cols
        assert "process" not in cols
        assert "model_tier" in cols

        loaded = await pg_db.get_chat_history(acct, uid)
        assert loaded[1]["model_tier"] == "Reasoning"          # label survives
        assert loaded[0]["model_tier"] == ""                   # user row clean

    async def test_legacy_plaintext_rows_still_readable(self, pg_db, _encryption_on):
        acct, owner = await _seed_owner(pg_db)
        uid = owner.telegram_id or owner.id
        # Simulate a pre-encryption row written directly.
        await pg_db._db.execute(
            "INSERT INTO ai_chat_history (account_id, user_id, role, text, created_at)"
            " VALUES (?, ?, 'user', ?, ?)",
            (acct.id, uid, "old plaintext question", pg_db._now()),
        )
        await pg_db._db.commit()

        rows = await pg_db.get_chat_history(acct.id, uid)
        assert rows[0]["text"] == "old plaintext question"

    async def test_wrong_key_degrades_to_placeholder_not_500(self, pg_db):
        from infra import crypto
        acct, owner = await _seed_owner(pg_db)
        uid = owner.telegram_id or owner.id

        os.environ["ENCRYPTION_KEY"] = "key-one"
        crypto.init_encryption()
        try:
            await pg_db.save_chat_messages(acct.id, uid, "secret q", "secret a")
            os.environ["ENCRYPTION_KEY"] = "key-two-rotated"
            crypto.init_encryption()

            rows = await pg_db.get_chat_history(acct.id, uid)
            assert all("unreadable" in r["text"] for r in rows)
            assert not any("secret" in r["text"] for r in rows)
        finally:
            os.environ["ENCRYPTION_KEY"] = ""
            crypto.init_encryption()


class TestAgeCap:
    async def test_prune_deletes_only_rows_older_than_window(self, pg_db):
        from datetime import datetime, timedelta, timezone
        acct, owner = await _seed_owner(pg_db)
        uid = owner.telegram_id or owner.id

        old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        await pg_db._db.execute(
            "INSERT INTO ai_chat_history (account_id, user_id, role, text, created_at)"
            " VALUES (?, ?, 'user', 'ancient', ?)", (acct.id, uid, old_ts),
        )
        await pg_db._db.commit()
        await pg_db.save_chat_messages(acct.id, uid, "fresh q", "fresh a")

        deleted = await pg_db.prune_ai_chat_history(days=90)

        assert deleted == 1
        rows = await pg_db.get_chat_history(acct.id, uid)
        assert [r["text"] for r in rows] == ["fresh q", "fresh a"]

    def test_retention_hub_registers_the_target_and_need(self):
        from capabilities.data_lifecycle.retention import discover
        from capabilities.data_lifecycle.retention.registry import resolve
        discover()
        resolved = {r.target.key: r for r in resolve(scope="platform")}
        assert "ai.chat_history" in resolved
        assert resolved["ai.chat_history"].keep_days == 90


class TestRemovalCascade:
    async def test_remove_user_hard_deletes_their_chat_only(self, pg_db):
        from interfaces.api.auth import _hash_password
        acct, owner = await _seed_owner(pg_db)
        colleague = await pg_db.create_user_with_email(
            email=f"dispatcher.{acct.id}@example.com",
            password_hash=_hash_password("dispatchpass1"),
            account_id=acct.id,
            role=Role.DISPATCHER,
            display_name="Dispatcher",
        )
        owner_uid = owner.telegram_id or owner.id
        # Email-only colleague: JWT sub falls back to users.id.
        colleague_uid = colleague.telegram_id or colleague.id

        await pg_db.save_chat_messages(acct.id, owner_uid, "owner q", "owner a")
        await pg_db.save_chat_messages(acct.id, colleague_uid, "их q", "их a")

        await pg_db.remove_user(colleague.id)

        assert await pg_db.get_chat_history(acct.id, colleague_uid) == []
        # The other user's history is untouched.
        assert len(await pg_db.get_chat_history(acct.id, owner_uid)) == 2
