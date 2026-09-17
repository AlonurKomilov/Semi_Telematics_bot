"""Database constraints and Team mutation ordering for Chat's shared DB."""
import asyncio
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from adapters.storage.chat_schema import migrate_chat
from adapters.storage.models import Role

pytestmark = [pytest.mark.asyncio, pytest.mark.security]


@pytest_asyncio.fixture
async def data(pg_db):
    a = await pg_db.create_account('Chat A')
    b = await pg_db.create_account('Chat B')
    owner = await pg_db.create_user(91001, a.id, role=Role.OWNER)
    member = await pg_db.create_user(91002, a.id, role=Role.FLEET)
    outsider = await pg_db.create_user(91003, b.id, role=Role.FLEET)
    async with pg_db.transaction():
        await pg_db.chat_lock_account(a.id)
        cid = await pg_db.chat_insert_conversation(a.id, kind='roles', title='Room', owner_user_id=owner.id,
            role_keys=['owner', 'fleet'], new_member_history='since_join')
    return pg_db, a, b, owner, member, outsider, cid


async def message(db, aid, cid, uid, seq=1, reply=None):
    cur = await db._db.execute(
        'INSERT INTO chat_messages (account_id, conversation_id, author_id, client_message_id, message_seq, body, reply_to_id) '
        'VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id', (aid, cid, uid, str(uuid4()), seq, 'Hello', reply))
    return (await cur.fetchone())['id']


async def test_migration_is_idempotent_and_preserves_data(data):
    db, a, _, _, member, _, cid = data
    before = await db.chat_membership(a.id, cid, member.id)
    await migrate_chat(db._db)
    await migrate_chat(db._db)
    assert await db.chat_membership(a.id, cid, member.id) == before
    assert (await db.chat_get_conversation(a.id, cid))['title'] == 'Room'


@pytest.mark.parametrize('operation', ['author', 'admin', 'member', 'state', 'draft', 'owner', 'mention', 'pin', 'delivery'])
async def test_foreign_tenant_user_is_rejected_by_database(data, operation):
    db, a, _, owner, _, foreign, cid = data
    mid = await message(db, a.id, cid, owner.id)
    cur = await db._db.execute('INSERT INTO chat_events (account_id, conversation_id, event_seq, kind, resource_version) '
                               'VALUES (?, ?, 1, ?, 1) RETURNING id', (a.id, cid, 'message_created'))
    eid = (await cur.fetchone())['id']
    queries = {
        'author': ('INSERT INTO chat_messages (account_id, conversation_id, author_id, client_message_id, message_seq, body) VALUES (?, ?, ?, ?, 2, ?) RETURNING id', (a.id, cid, foreign.id, 'retry', 'test')),
        'admin': ('INSERT INTO chat_group_admins (account_id, conversation_id, user_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING', (a.id, cid, foreign.id)),
        'member': ('INSERT INTO chat_membership_intervals (account_id, conversation_id, user_id, visible_after_message_seq) VALUES (?, ?, ?, 0) RETURNING id', (a.id, cid, foreign.id)),
        'state': ('INSERT INTO chat_member_state (account_id, conversation_id, user_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING', (a.id, cid, foreign.id)),
        'draft': ('INSERT INTO chat_drafts (account_id, conversation_id, user_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING', (a.id, cid, foreign.id)),
        'owner': ('UPDATE chat_conversations SET owner_user_id = ? WHERE account_id = ? AND id = ?', (foreign.id, a.id, cid)),
        'mention': ('INSERT INTO chat_mentions (account_id, conversation_id, message_id, user_id) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING', (a.id, cid, mid, foreign.id)),
        'pin': ('INSERT INTO chat_pins (account_id, conversation_id, message_id, pinned_by) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING', (a.id, cid, mid, foreign.id)),
        'delivery': ('INSERT INTO chat_event_deliveries (account_id, conversation_id, event_id, channel, recipient_user_id) VALUES (?, ?, ?, ?, ?) RETURNING id', (a.id, cid, eid, 'in_app', foreign.id)),
    }
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db._db.execute(*queries[operation])


async def test_reply_pin_and_event_cannot_reference_another_conversation(data):
    db, a, _, owner, _, _, cid = data
    other = await db.chat_insert_conversation(a.id, kind='account', title='Other', owner_user_id=owner.id)
    mid = await message(db, a.id, other, owner.id)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await message(db, a.id, cid, owner.id, reply=mid)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db._db.execute('INSERT INTO chat_pins (account_id, conversation_id, message_id, pinned_by) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING',
                             (a.id, cid, mid, owner.id))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db._db.execute('INSERT INTO chat_events (account_id, conversation_id, event_seq, kind, resource_version, message_id) VALUES (?, ?, 1, ?, 1, ?) RETURNING id',
                             (a.id, cid, 'message_created', mid))


async def test_canonical_pair_general_and_identity_constraints(data):
    db, a, _, owner, member, _, cid = data
    direct = await db.chat_insert_conversation(a.id, kind='direct', direct_pair=(owner.id, member.id))
    cur = await db._db.execute('SELECT user_id FROM chat_participants WHERE account_id = ? AND conversation_id = ?', (a.id, direct))
    assert {r['user_id'] for r in await cur.fetchall()} == {owner.id, member.id}
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.chat_insert_conversation(a.id, kind='direct', direct_pair=(owner.id, member.id))
    with pytest.raises(asyncpg.CheckViolationError):
        await db.chat_insert_conversation(a.id, kind='direct', direct_pair=(member.id, owner.id))
    general = await db.chat_insert_conversation(a.id, kind='account', title='General', owner_user_id=owner.id, system_key='general')
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.chat_insert_conversation(a.id, kind='account', title='General', owner_user_id=owner.id, system_key='general')
    with pytest.raises(asyncpg.CheckViolationError):
        await db._db.execute('UPDATE chat_conversations SET archived = TRUE WHERE account_id = ? AND id = ?', (a.id, general))
    with pytest.raises(asyncpg.CheckViolationError):
        await db._db.execute("UPDATE chat_conversations SET kind = 'account' WHERE account_id = ? AND id = ?", (a.id, cid))
    third = await db.create_user(91004, a.id, role=Role.HR)
    with pytest.raises(asyncpg.CheckViolationError):
        await db._db.execute('INSERT INTO chat_participants (account_id, conversation_id, user_id) VALUES (?, ?, ?) ON CONFLICT DO NOTHING',
                             (a.id, direct, third.id))


async def test_team_transaction_rollback_restores_history_interval(data):
    db, a, _, _, member, _, cid = data
    before = await db.chat_membership(a.id, cid, member.id)
    with pytest.raises(RuntimeError, match='rollback'):
        async with db.transaction():
            await db._db.execute('UPDATE users SET is_active = 0 WHERE account_id = ? AND id = ?', (a.id, member.id))
            assert await db.chat_membership(a.id, cid, member.id) is None
            raise RuntimeError('rollback')
    assert await db.chat_membership(a.id, cid, member.id) == before


async def test_join_waits_for_prior_message_commit_and_uses_committed_sequence(data):
    db, a, _, _, member, _, cid = data
    await db._db.execute("UPDATE users SET role = 'hr' WHERE account_id = ? AND id = ?", (a.id, member.id))
    locked, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def commit_message():
        async with db.transaction():
            await db.chat_lock_account(a.id)
            await db._db.execute('UPDATE chat_conversations SET message_seq = 42 WHERE account_id = ? AND id = ?', (a.id, cid))
            locked.set()
            await release.wait()

    async def rejoin():
        await locked.wait()
        started.set()
        await db._db.execute("UPDATE users SET role = 'fleet' WHERE account_id = ? AND id = ?", (a.id, member.id))

    writer, joiner = asyncio.create_task(commit_message()), asyncio.create_task(rejoin())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        assert not joiner.done()
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(writer, joiner), timeout=10)
    assert (await db.chat_membership(a.id, cid, member.id))['visible_after_message_seq'] == 42


async def test_audience_updates_preserve_retained_intervals_and_stamp_new_members(data):
    db, a, _, _, member, _, cid = data
    before = await db.chat_membership(a.id, cid, member.id)
    newcomer = await db.create_user(91005, a.id, role=Role.DISPATCHER)
    async with db.transaction():
        await db.chat_lock_account(a.id)
        await db._db.execute('UPDATE chat_conversations SET message_seq = 7 WHERE account_id = ? AND id = ?', (a.id, cid))
        await db.chat_replace_roles(a.id, cid, ['owner', 'fleet', 'dispatcher'])
    assert await db.chat_membership(a.id, cid, member.id) == before
    assert (await db.chat_membership(a.id, cid, newcomer.id))['visible_after_message_seq'] == 7
    # Generic UPDATE is covered too, not only the service's replace helper.
    await db._db.execute("UPDATE chat_conversation_roles SET role_key = 'hr' WHERE account_id = ? AND conversation_id = ? AND role_key = 'dispatcher'", (a.id, cid))
    assert await db.chat_membership(a.id, cid, newcomer.id) is None


async def test_user_creation_stamps_boundary_and_account_move_cannot_move_history(data):
    db, a, b, _, member, _, cid = data
    await db._db.execute('UPDATE chat_conversations SET message_seq = 11 WHERE account_id = ? AND id = ?', (a.id, cid))
    newcomer = await db.create_user(91006, a.id, role=Role.FLEET)
    assert (await db.chat_membership(a.id, cid, newcomer.id))['visible_after_message_seq'] == 11
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db._db.execute('UPDATE users SET account_id = ? WHERE id = ?', (b.id, member.id))


async def test_user_mixin_mutations_share_the_transactional_projection(data):
    db, a, _, _, member, _, cid = data
    assert await db.update_user(member.id, role=Role.HR)
    assert await db.chat_membership(a.id, cid, member.id) is None
    assert await db.update_user(member.id, role=Role.FLEET)
    assert await db.chat_membership(a.id, cid, member.id) is not None
    assert await db.remove_user(member.id)
    assert await db.chat_membership(a.id, cid, member.id) is None


# ── the second wall: every Chat table carries tenant_isolation ─────

def test_every_chat_table_is_under_the_rls_migration():
    """The policy is applied from a LIST.  A fourteenth table added to
    SCHEMA_SQL without joining that list would hold private messages
    with no row-level wall — silently, because ENABLE_RLS is off in
    every test run.  So the list is checked against the DDL, not the
    database."""
    import re
    from adapters.storage import chat_schema as cs
    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (chat_\w+)", cs.SCHEMA_SQL))
    for extra in ("MESSAGE_API_SQL", "REALTIME_SQL"):
        declared |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (chat_\w+)", getattr(cs, extra, "") or ""))
    assert declared, "no chat tables found in the DDL — the regex or the constant moved"
    # The delivery queue is claimed across accounts by the outbox drain; a
    # fail-closed policy on it would leave realtime finding nothing.  It is
    # named as the exception so that it is a decision, not an omission.
    assert set(cs.CHAT_CROSS_TENANT_TABLES) == {"chat_event_deliveries"}
    assert set(cs.CHAT_TENANT_TABLES) == declared - set(cs.CHAT_CROSS_TENANT_TABLES), (
        f"tables without the policy: {sorted(declared - set(cs.CHAT_TENANT_TABLES) - set(cs.CHAT_CROSS_TENANT_TABLES))}; "
        f"listed but not declared: {sorted(set(cs.CHAT_TENANT_TABLES) - declared)}")
    assert not set(cs.CHAT_TENANT_TABLES) & set(cs.CHAT_CROSS_TENANT_TABLES)


def test_the_rls_migration_is_registered_after_the_tables_exist():
    """215 runs after 212–214 create the tables; a policy on a table that
    is not there yet is an error the loop would swallow into a log."""
    from adapters.storage import migrations as m
    names = [name for name, _fn in m._MIGRATIONS]
    idx = {n: i for i, n in enumerate(names)}
    assert "215_chat_rls" in idx
    for earlier in ("212_chat_foundation", "213_chat_message_api", "214_chat_realtime"):
        assert idx[earlier] < idx["215_chat_rls"], (earlier, "must run before the policy")
