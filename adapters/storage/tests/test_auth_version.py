"""Database security transitions are atomic for every writer."""
import pytest

from adapters.storage import Role
from adapters.storage.platform_migrations import migrate_user_auth_version

pytestmark = [pytest.mark.security, pytest.mark.asyncio]


async def test_migration_is_idempotent_and_direct_sql_invalidates(pg_db):
    account = await pg_db.create_account('Version migration')
    user = await pg_db.create_user(900041, account.id, role=Role.OWNER)
    await migrate_user_auth_version(pg_db._db)
    await migrate_user_auth_version(pg_db._db)
    assert (await pg_db.get_user(user.id)).auth_version == 0
    await pg_db._db.execute('UPDATE users SET role = ? WHERE id = ?', ('driver', user.id))
    assert (await pg_db.get_user(user.id)).auth_version == 1
    await pg_db.update_user(user.id, role='driver', display_name='Harmless change')
    assert (await pg_db.get_user(user.id)).auth_version == 1


async def test_rollback_restores_password_version_and_sessions(pg_db):
    account = await pg_db.create_account('Atomic invalidation')
    user = await pg_db.create_user_with_email('rollback@example.invalid', 'old-hash', account.id)
    await pg_db.create_user_session(user_id=user.id, jti='rollback-session',
        device_label='Test', user_agent='Test', ip='127.0.0.1', created_at=pg_db._now(),
        last_seen=pg_db._now(), expires_at='2099-01-01T00:00:00+00:00')
    with pytest.raises(RuntimeError, match='rollback'):
        async with pg_db.transaction():
            await pg_db.set_user_email_password(user.id, user.email, 'new-hash')
            changed = await pg_db.get_user_auth_state(user.id, 'rollback-session')
            assert changed['auth_version'] == 1
            assert changed['revoked_at']
            raise RuntimeError('rollback')
    restored = await pg_db.get_user(user.id)
    assert restored.password_hash == 'old-hash'
    assert restored.auth_version == 0
    assert not (await pg_db.get_user_auth_state(user.id, 'rollback-session'))['revoked_at']


async def test_revoke_all_invalidates_even_unrecorded_tokens(pg_db):
    account = await pg_db.create_account('All sessions')
    user = await pg_db.create_user(900042, account.id)
    assert await pg_db.revoke_other_user_sessions(user.id, '') == []
    assert (await pg_db.get_user(user.id)).auth_version == 1
